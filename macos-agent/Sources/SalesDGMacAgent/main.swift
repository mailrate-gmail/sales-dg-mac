import AppKit
import AVFoundation
import Carbon
import Foundation
import Speech

enum AgentState {
    case stopped
    case loading
    case running
    case paused
    case error
}

struct AgentConfig: Codable {
    struct Speaker: Codable {
        var key: Int
        var name: String
        var participantId: String?
        var role: String
    }

    var backendBaseURL: String
    var meetingId: String
    var ingestToken: String?
    var localeIdentifier: String
    var speakers: [Speaker]

    static let path = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".sales-dg-mac-agent.json")

    static func sample() -> AgentConfig {
        AgentConfig(
            backendBaseURL: "http://localhost:8000",
            meetingId: "session_xxx",
            ingestToken: nil,
            localeIdentifier: "zh-CN",
            speakers: [
                Speaker(key: 1, name: "客户一", participantId: nil, role: "customer"),
                Speaker(key: 2, name: "我方", participantId: nil, role: "self"),
                Speaker(key: 3, name: "其他", participantId: nil, role: "other")
            ]
        )
    }

    static func loadOrCreateSample() -> AgentConfig {
        let decoder = JSONDecoder()
        if let data = try? Data(contentsOf: path), let config = try? decoder.decode(AgentConfig.self, from: data) {
            return config
        }

        let config = sample()
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        if let data = try? encoder.encode(config) {
            try? data.write(to: path, options: .atomic)
        }
        return config
    }
}

@MainActor
final class TranscriptWindowController: NSObject {
    private let window: NSWindow
    private let textView: NSTextView
    private let speakerLabel: NSTextField
    private let statusLabel: NSTextField

    override init() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 760, height: 480),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Sales-DG 语音识别状态"
        window.center()

        let content = NSView(frame: window.contentView?.bounds ?? .zero)
        content.autoresizingMask = [.width, .height]

        speakerLabel = NSTextField(labelWithString: "当前发言人：未选择")
        speakerLabel.frame = NSRect(x: 16, y: 438, width: 360, height: 22)

        statusLabel = NSTextField(labelWithString: "状态：停止")
        statusLabel.frame = NSRect(x: 390, y: 438, width: 340, height: 22)

        let scrollView = NSScrollView(frame: NSRect(x: 16, y: 16, width: 728, height: 410))
        scrollView.autoresizingMask = [.width, .height]
        scrollView.hasVerticalScroller = true
        textView = NSTextView(frame: scrollView.bounds)
        textView.isEditable = false
        textView.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        scrollView.documentView = textView

        content.addSubview(speakerLabel)
        content.addSubview(statusLabel)
        content.addSubview(scrollView)
        window.contentView = content
    }

    func show() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func setSpeaker(_ speaker: AgentConfig.Speaker?) {
        speakerLabel.stringValue = "当前发言人：\(speaker?.name ?? "未选择")"
    }

    func setStatus(_ text: String) {
        statusLabel.stringValue = "状态：\(text)"
    }

    func append(_ text: String) {
        let line = "\(DateFormatter.logTime.string(from: Date()))  \(text)\n"
        textView.textStorage?.append(NSAttributedString(string: line))
        textView.scrollToEndOfDocument(nil)
    }
}

extension DateFormatter {
    static let logTime: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter
    }()
}

@MainActor
final class TranscriptClient {
    private let config: AgentConfig
    private let window: TranscriptWindowController

    init(config: AgentConfig, window: TranscriptWindowController) {
        self.config = config
        self.window = window
    }

    func post(text: String, speaker: AgentConfig.Speaker?) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        guard var components = URLComponents(string: config.backendBaseURL) else {
            window.append("后端地址无效：\(config.backendBaseURL)")
            return
        }
        components.path = "/api/meetings/\(config.meetingId)/transcripts"
        guard let url = components.url else { return }

        var payload: [String: Any] = [
            "text": text,
            "source": "macos_speech_asr",
            "recognized_at": ISO8601DateFormatter().string(from: Date())
        ]
        if let speaker {
            payload["speaker_name"] = speaker.name
            payload["role"] = speaker.role
            if let participantId = speaker.participantId, !participantId.isEmpty {
                payload["participant_id"] = participantId
            }
        } else {
            payload["speaker_name"] = "macOS ASR"
            payload["role"] = "unknown"
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token = config.ingestToken, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)

        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            Task { @MainActor in
                if let error {
                    self?.window.append("写入后端失败：\(error.localizedDescription)")
                    return
                }
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                if !(200...299).contains(status) {
                    let body = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
                    self?.window.append("写入后端失败 HTTP \(status)：\(body)")
                    return
                }
                self?.window.append("已写入后端：\(text)")
            }
        }.resume()
    }
}

@MainActor
final class SpeechController {
    private let config: AgentConfig
    private let window: TranscriptWindowController
    private let transcriptClient: TranscriptClient
    private let onStateChange: (AgentState) -> Void

    private let audioEngine = AVAudioEngine()
    private var speechRecognizer: SFSpeechRecognizer?
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private var running = false
    private var paused = false
    private var currentSpeaker: AgentConfig.Speaker?
    private var lastPartial = ""

    init(
        config: AgentConfig,
        window: TranscriptWindowController,
        transcriptClient: TranscriptClient,
        onStateChange: @escaping (AgentState) -> Void
    ) {
        self.config = config
        self.window = window
        self.transcriptClient = transcriptClient
        self.onStateChange = onStateChange
        self.speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: config.localeIdentifier))
    }

    func setSpeaker(_ speaker: AgentConfig.Speaker?) {
        currentSpeaker = speaker
        window.setSpeaker(speaker)
        window.append("切换发言人：\(speaker?.name ?? "未选择")")
    }

    func start() {
        guard !running else { return }
        paused = false
        requestPermissions { [weak self] granted in
            guard let self else { return }
            if granted {
                self.beginRecognition()
            } else {
                self.window.append("麦克风或语音识别权限未授权")
                self.onStateChange(.error)
            }
        }
    }

    func togglePause() {
        if paused {
            paused = false
            window.append("继续识别")
            beginRecognition()
        } else {
            paused = true
            stopRecognition(keepRunningFlag: false)
            window.append("已暂停")
            onStateChange(.paused)
        }
    }

    func stop() {
        paused = false
        stopRecognition(keepRunningFlag: false)
        window.append("已停止")
        onStateChange(.stopped)
    }

    private func requestPermissions(_ completion: @escaping @MainActor (Bool) -> Void) {
        onStateChange(.loading)
        SFSpeechRecognizer.requestAuthorization { status in
            AVCaptureDevice.requestAccess(for: .audio) { micGranted in
                Task { @MainActor in
                    completion(status == .authorized && micGranted)
                }
            }
        }
    }

    private func beginRecognition() {
        guard !paused else { return }
        guard let speechRecognizer, speechRecognizer.isAvailable else {
            window.append("系统语音识别当前不可用")
            onStateChange(.error)
            return
        }

        stopRecognition(keepRunningFlag: true)
        running = true
        lastPartial = ""
        onStateChange(.running)
        window.append("开始识别，语言：\(config.localeIdentifier)")

        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let recognitionRequest else { return }
        recognitionRequest.shouldReportPartialResults = true
        if #available(macOS 13.0, *) {
            recognitionRequest.addsPunctuation = true
        }

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            recognitionRequest.append(buffer)
        }

        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            window.append("麦克风启动失败：\(error.localizedDescription)")
            onStateChange(.error)
            return
        }

        recognitionTask = speechRecognizer.recognitionTask(with: recognitionRequest) { [weak self] result, error in
            Task { @MainActor in
                guard let self else { return }
                if let result {
                    let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
                    if text != self.lastPartial {
                        self.lastPartial = text
                        self.window.append("识别中：\(text)")
                    }
                    if result.isFinal {
                        self.transcriptClient.post(text: text, speaker: self.currentSpeaker)
                        self.restartIfNeeded()
                    }
                }
                if let error {
                    self.window.append("识别任务结束：\(error.localizedDescription)")
                    self.restartIfNeeded()
                }
            }
        }
    }

    private func restartIfNeeded() {
        guard running && !paused else { return }
        stopRecognition(keepRunningFlag: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
            self?.beginRecognition()
        }
    }

    private func stopRecognition(keepRunningFlag: Bool) {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil
        running = keepRunningFlag
    }
}

@MainActor
final class HotKeyManager {
    private var hotKeys: [EventHotKeyRef?] = []
    private var handler: EventHandlerRef?
    private let onSpeakerKey: (Int) -> Void
    private let onPause: () -> Void
    private let onQuit: () -> Void
    private let onShowWindow: () -> Void

    init(
        onSpeakerKey: @escaping (Int) -> Void,
        onPause: @escaping () -> Void,
        onQuit: @escaping () -> Void,
        onShowWindow: @escaping () -> Void
    ) {
        self.onSpeakerKey = onSpeakerKey
        self.onPause = onPause
        self.onQuit = onQuit
        self.onShowWindow = onShowWindow
    }

    func install() {
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let selfPointer = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, event, userData in
            guard let event, let userData else { return noErr }
            var hotKeyID = EventHotKeyID()
            GetEventParameter(
                event,
                EventParamName(kEventParamDirectObject),
                EventParamType(typeEventHotKeyID),
                nil,
                MemoryLayout<EventHotKeyID>.size,
                nil,
                &hotKeyID
            )
            let manager = Unmanaged<HotKeyManager>.fromOpaque(userData).takeUnretainedValue()
            manager.handle(id: Int(hotKeyID.id))
            return noErr
        }, 1, &eventType, selfPointer, &handler)

        for (index, keyCode) in Self.functionKeyCodes.prefix(10).enumerated() {
            register(id: index + 1, keyCode: keyCode, modifiers: 0)
        }
        register(id: 11, keyCode: Self.keyCodeF11, modifiers: 0)
        register(id: 12, keyCode: Self.keyCodeF12, modifiers: 0)
        register(id: 101, keyCode: Self.keyCodeE, modifiers: UInt32(controlKey | optionKey))
    }

    private func register(id: Int, keyCode: UInt32, modifiers: UInt32) {
        var hotKeyRef: EventHotKeyRef?
        let hotKeyID = EventHotKeyID(signature: OSType(0x53444741), id: UInt32(id)) // SDGA
        let status = RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)
        if status == noErr {
            hotKeys.append(hotKeyRef)
        }
    }

    private func handle(id: Int) {
        switch id {
        case 1...10:
            onSpeakerKey(id)
        case 11:
            onPause()
        case 12:
            onQuit()
        case 101:
            onShowWindow()
        default:
            break
        }
    }

    private static let functionKeyCodes: [UInt32] = [122, 120, 99, 118, 96, 97, 98, 100, 101, 109]
    private static let keyCodeF11: UInt32 = 103
    private static let keyCodeF12: UInt32 = 111
    private static let keyCodeE: UInt32 = 14
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var config: AgentConfig!
    private var windowController: TranscriptWindowController!
    private var transcriptClient: TranscriptClient!
    private var speechController: SpeechController!
    private var hotKeyManager: HotKeyManager!

    func applicationDidFinishLaunching(_ notification: Notification) {
        config = AgentConfig.loadOrCreateSample()
        windowController = TranscriptWindowController()
        transcriptClient = TranscriptClient(config: config, window: windowController)
        speechController = SpeechController(config: config, window: windowController, transcriptClient: transcriptClient) { [weak self] state in
            self?.setState(state)
        }

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = statusImage(.stopped)
        statusItem.button?.toolTip = "Sales-DG Mac Agent"
        statusItem.menu = makeMenu()

        hotKeyManager = HotKeyManager(
            onSpeakerKey: { [weak self] index in self?.selectSpeaker(index) },
            onPause: { [weak self] in self?.speechController.togglePause() },
            onQuit: { NSApp.terminate(nil) },
            onShowWindow: { [weak self] in self?.windowController.show() }
        )
        hotKeyManager.install()

        windowController.setStatus("停止")
        windowController.append("配置文件：\(AgentConfig.path.path)")
        windowController.append("F1-F10 切换发言人，F11 暂停/继续，F12 退出，⌃⌥E 打开窗口")

        if config.meetingId == "session_xxx" {
            windowController.show()
            windowController.append("请先编辑配置文件中的 meetingId 和 speakers。")
        }
        speechController.start()
    }

    private func makeMenu() -> NSMenu {
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "打开识别窗口（⌃⌥E）", action: #selector(showWindow), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "暂停 / 继续（F11）", action: #selector(togglePause), keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "退出（F12）", action: #selector(quit), keyEquivalent: ""))
        return menu
    }

    @objc private func showWindow() {
        windowController.show()
    }

    @objc private func togglePause() {
        speechController.togglePause()
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func selectSpeaker(_ key: Int) {
        let speaker = config.speakers.first { $0.key == key }
        speechController.setSpeaker(speaker)
    }

    private func setState(_ state: AgentState) {
        statusItem.button?.image = statusImage(state)
        switch state {
        case .stopped:
            windowController.setStatus("停止")
        case .loading:
            windowController.setStatus("加载中")
        case .running:
            windowController.setStatus("运行")
        case .paused:
            windowController.setStatus("暂停")
        case .error:
            windowController.setStatus("异常")
        }
    }

    private func statusImage(_ state: AgentState) -> NSImage {
        let color: NSColor
        switch state {
        case .running:
            color = .systemGreen
        case .loading, .paused:
            color = .systemYellow
        case .stopped, .error:
            color = .systemRed
        }
        let image = NSImage(size: NSSize(width: 18, height: 18))
        image.lockFocus()
        color.setFill()
        NSBezierPath(ovalIn: NSRect(x: 3, y: 3, width: 12, height: 12)).fill()
        image.unlockFocus()
        image.isTemplate = false
        return image
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
