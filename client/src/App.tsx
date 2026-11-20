import { useEffect, useMemo, useRef, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''
const VOICE_GAP_SPLIT_MS = 800
const VOICE_ENERGY_TRIGGER = 0.008
const SENTENCE_END = '。！？!?；;'
const ANY_PUNCTUATION = '，,。.!！?？；;：:、'
const QUESTION_MARKERS = ['吗', '呢', '么', '什么', '为什么', '怎么', '如何', '是否', '能否', '能不能', '可不可以', '有没有', '多少', '什么时候', '请问']
const COMMA_MARKERS = ['但是', '不过', '所以', '因此', '然后', '另外', '同时', '而且', '如果', '其实', '比如', '例如', '也就是说', '一方面', '另一方面', '首先', '其次', '最后']
const SPLIT_CANDIDATES = ['因为', '所以', '但是', '然后', '另外', '同时', '如果', '我们', '客户', '现在', '目前', '这个', '那个', '可以', '需要', '希望', '已经', '就是', '的话', '的时候', '之后']
const MAX_PLAIN_CLAUSE_LENGTH = 42

type ModelLoadPhase = 'stopped' | 'loading' | 'running'

function escapeRegExp(text: string) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function hasAnyPunctuation(text: string) {
  return Array.from(text).some((char) => ANY_PUNCTUATION.includes(char))
}

function isQuestionLike(text: string) {
  return text.includes('?') || text.includes('？') || QUESTION_MARKERS.some((marker) => text.includes(marker))
}

function splitLongPlainClause(text: string, limit: number) {
  if (text.length <= limit) return text
  const parts: string[] = []
  let rest = text
  while (rest.length > limit) {
    const start = Math.max(8, limit - 8)
    const end = Math.min(rest.length, limit + 8)
    let splitAt = -1
    for (const marker of SPLIT_CANDIDATES) {
      const idx = rest.slice(start, end).lastIndexOf(marker)
      if (idx >= 0 && start + idx > 6) {
        splitAt = start + idx
        break
      }
    }
    if (splitAt <= 6) splitAt = limit
    parts.push(rest.slice(0, splitAt))
    rest = rest.slice(splitAt)
  }
  if (rest) parts.push(rest)
  return parts.join('，')
}

function splitLongClauses(text: string) {
  const limit = MAX_PLAIN_CLAUSE_LENGTH
  let result = ''
  let current = ''
  for (const char of Array.from(text)) {
    if (ANY_PUNCTUATION.includes(char)) {
      if (current) {
        result += splitLongPlainClause(current, limit)
        current = ''
      }
      result += char
    } else {
      current += char
    }
  }
  if (current) result += splitLongPlainClause(current, limit)
  return result
}

function displayTranscriptText(text: string) {
  let value = text.replace(/\s+/g, ' ').trim()
  if (!value) return ''

  if (!hasAnyPunctuation(value)) {
    for (const marker of COMMA_MARKERS) {
      const pattern = new RegExp(`(?<![${escapeRegExp(ANY_PUNCTUATION)}])(${escapeRegExp(marker)})`, 'g')
      value = value.replace(pattern, '，$1')
    }
    value = value.replace(/^，+/, '')
  }
  value = splitLongClauses(value)

  const last = value[value.length - 1]
  if (!SENTENCE_END.includes(last)) {
    value += isQuestionLike(value) ? '？' : '。'
  }
  return value
}

function formatMessageTime(value?: string) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

type Role = 'self' | 'customer' | 'other' | 'unknown'
type Page = 'new' | 'history' | 'modelSettings' | 'asrSettings' | 'voiceSettings' | 'themeSettings' | 'room'
type Theme = 'dark' | 'light' | 'blue'

interface AppRoute {
  page: Page
  meetingId?: string
}

function routeFromValue(value = '/new'): AppRoute {
  value = value || '/new'
  if (value === '/history') return { page: 'history' }
  if (value === '/settings/model') return { page: 'modelSettings' }
  if (value === '/settings/asr') return { page: 'asrSettings' }
  if (value === '/settings/voice') return { page: 'voiceSettings' }
  if (value === '/settings/theme') return { page: 'themeSettings' }
  if (value === '/room') return { page: 'room' }
  const meetingMatch = value.match(/^\/meetings\/([^/]+)$/)
  if (meetingMatch) return { page: 'room', meetingId: decodeURIComponent(meetingMatch[1]) }
  return { page: 'new' }
}

function routeFromLocation(): AppRoute {
  if (window.location.hash.startsWith('#/')) {
    return routeFromValue(window.location.hash.replace(/^#/, ''))
  }
  return routeFromValue(window.location.pathname || '/new')
}

function pathForRoute(page: Page, meetingId?: string) {
  if (page === 'history') return '/history'
  if (page === 'modelSettings') return '/settings/model'
  if (page === 'asrSettings') return '/settings/asr'
  if (page === 'voiceSettings') return '/settings/voice'
  if (page === 'themeSettings') return '/settings/theme'
  if (page === 'room') return meetingId ? `/meetings/${encodeURIComponent(meetingId)}` : '/room'
  return '/new'
}

interface Participant {
  id: string
  name: string
  role: Role
  need_reply: boolean
  display_color: string
  voiceprint_file?: string
  remark: string
}

interface Message {
  id: string
  session_id: string
  participant_id?: string | null
  speaker_identity_id?: string | null
  speaker_name: string
  role: Role
  text: string
  speaker_score?: number
  need_reply: boolean
  ai_suggestion?: string
  corrected: boolean
  start_time?: number | null
  end_time?: number | null
  created_at?: string
}

interface SessionDetail {
  id: string
  title: string
  goal: string
  started_at: string
  ended_at?: string | null
  messages: Message[]
  summary?: string | null
}

interface SessionSummary {
  id: string
  title: string
  goal: string
  started_at: string
  ended_at?: string | null
  message_count: number
  has_summary: boolean
}

interface ModelConfig {
  llm_provider: string
  llm_model: string
  llm_base_url: string
  llm_api_key: string
  llm_api_type: 'chat_completions' | 'responses'
  llm_timeout_seconds: number
  model_availability: Record<string, boolean>
  custom_models: CustomModelConfig[]
  asr_provider: 'funasr_streaming' | 'faster_whisper' | 'oci_speech'
  whisper_model: string
  whisper_device: string
  whisper_compute_type: string
  bilingual_asr_enabled: boolean
  funasr_model: string
  funasr_device: string
  oci_speech_region: string
  oci_speech_compartment_id: string
  oci_speech_language: string
  oci_speech_profile: string
  oci_speech_config_file: string
  oci_speech_realtime_endpoint: string
  speaker_threshold: number
  speaker_cluster_threshold: number
  speaker_diarization_enabled: boolean
  energy_threshold: number
  silence_duration_ms: number
  min_speech_duration_ms: number
  punctuation_provider: 'asr' | 'funasr_punc' | 'rule' | 'off'
  punctuation_model: string
  punctuation_device: string
}

interface CustomModelConfig {
  id: string
  name: string
  model: string
  base_url: string
  api_type: 'chat_completions' | 'responses'
  description: string
}

interface ModelPreset {
  id: string
  name: string
  model: string
  baseUrl: string
  apiType: 'chat_completions' | 'responses'
  description: string
  custom?: boolean
}

interface RecognizedSpeaker {
  id: string
  name: string
  role: Role
  count: number
  latestScore?: number
}

interface AiQaItem {
  id: string
  speakerName: string
  question: string
  answer: string
  status: 'thinking' | 'done' | 'error'
}

interface AgentDecision {
  id: string
  type: string
  reason: string
  content: string
  message_id?: string | null
  speaker_name: string
  created_at: string
}

interface AgentMemory {
  meeting_id: string
  goal: string
  short_term_context: string[]
  pain_points: string[]
  risks: string[]
  commitments: string[]
  todos: string[]
  questions: string[]
  decisions: AgentDecision[]
  updated_at: string
}

const defaultConfig: ModelConfig = {
  llm_provider: 'deepseek',
  llm_model: 'deepseek-chat',
  llm_base_url: 'https://api.deepseek.com',
  llm_api_key: '',
  llm_api_type: 'chat_completions',
  llm_timeout_seconds: 30,
  model_availability: {
    deepseek: true,
    gpt: false,
    qwen: false,
    grok: false,
  },
  custom_models: [],
  asr_provider: 'funasr_streaming',
  whisper_model: 'small',
  whisper_device: 'cpu',
  whisper_compute_type: 'int8',
  bilingual_asr_enabled: false,
  funasr_model: 'paraformer-zh-streaming',
  funasr_device: 'cpu',
  oci_speech_region: '',
  oci_speech_compartment_id: '',
  oci_speech_language: 'zh-CN',
  oci_speech_profile: 'DEFAULT',
  oci_speech_config_file: '/root/.oci/config',
  oci_speech_realtime_endpoint: '',
  speaker_threshold: 0.3,
  speaker_cluster_threshold: 0.45,
  speaker_diarization_enabled: false,
  energy_threshold: 0.008,
  silence_duration_ms: 800,
  min_speech_duration_ms: 1000,
  punctuation_provider: 'funasr_punc',
  punctuation_model: 'ct-punc',
  punctuation_device: 'cpu',
}

const modelPresets: ModelPreset[] = [
  {
    id: 'deepseek',
    name: 'DeepSeek',
    model: 'deepseek-chat',
    baseUrl: 'https://api.deepseek.com',
    apiType: 'chat_completions' as const,
    description: '适合中文商务会谈，成本较低。',
  },
  {
    id: 'gpt',
    name: 'GPT / OpenAI',
    model: 'gpt-4o-mini',
    baseUrl: 'https://api.openai.com',
    apiType: 'responses' as const,
    description: 'OpenAI Compatible 官方接口。',
  },
  {
    id: 'qwen',
    name: 'Qwen / 通义千问',
    model: 'qwen-plus',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode',
    apiType: 'chat_completions' as const,
    description: '阿里云 DashScope OpenAI 兼容模式。',
  },
  {
    id: 'grok',
    name: 'Grok / xAI',
    model: 'grok-3-mini',
    baseUrl: 'https://api.x.ai',
    apiType: 'responses' as const,
    description: 'xAI OpenAI Compatible 接口。',
  },
]

function AgentMemoryBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="agent-memory-block">
      <h3>{title}</h3>
      {items.length === 0 && <p className="empty">暂无</p>}
      {items.map((item, index) => <p key={`${title}_${index}`}>{item}</p>)}
    </div>
  )
}

export function App() {
  const [page, setPage] = useState<Page>(() => routeFromLocation().page)
  const [theme, setTheme] = useState<Theme>('dark')
  const [menuCollapsed, setMenuCollapsed] = useState(false)
  const [customerName, setCustomerName] = useState('')
  const [goal, setGoal] = useState('了解客户当前业务痛点，并推动下一次需求调研。')
  const [sessionId, setSessionId] = useState<string>('')
  const [sessionTitle, setSessionTitle] = useState('')
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [speakerNames, setSpeakerNames] = useState<Record<string, string>>({})
  const [speakerPanelStartIndex, setSpeakerPanelStartIndex] = useState(0)
  const [speakerPushTargets, setSpeakerPushTargets] = useState<Record<string, boolean>>({})
  const [aiQas, setAiQas] = useState<AiQaItem[]>([])
  const [agentMemory, setAgentMemory] = useState<AgentMemory | null>(null)
  const [latestSuggestion, setLatestSuggestion] = useState('')
  const [summary, setSummary] = useState('')
  const [recording, setRecording] = useState(false)
  const [speakerPanelOpen, setSpeakerPanelOpen] = useState(true)
  const [loadProgressOpen, setLoadProgressOpen] = useState(true)
  const [energyLevel, setEnergyLevel] = useState(0)
  const [recordingStatus, setRecordingStatus] = useState('未开始录音')
  const [modelLoadPhase, setModelLoadPhase] = useState<ModelLoadPhase>('stopped')
  const [modelLoadNote, setModelLoadNote] = useState('点击“开始录音”后显示模型加载状态')
  const [config, setConfig] = useState<ModelConfig>(defaultConfig)
  const [connectionTest, setConnectionTest] = useState('')
  const [testingConnection, setTestingConnection] = useState(false)

  const mediaStreamRef = useRef<MediaStream | null>(null)
  const websocketRef = useRef<WebSocket | null>(null)
  const streamingReadyRef = useRef(false)
  const audioSourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const audioProcessorRef = useRef<ScriptProcessorNode | null>(null)
  const segmentRecorderRef = useRef<MediaRecorder | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const animationFrameRef = useRef<number | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const recordingRef = useRef(false)
  const speechActiveRef = useRef(false)
  const segmentStartedAtRef = useRef(0)
  const lastVoiceAtRef = useRef(0)
  const activeSessionIdRef = useRef('')
  const configRef = useRef(config)
  const initialMeetingIdRef = useRef(routeFromLocation().meetingId || '')

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => {
    document.body.dataset.theme = theme
  }, [theme])

  useEffect(() => {
    const route = routeFromLocation()
    if (window.location.hash.startsWith('#/') || window.location.pathname === '/') {
      const nextPath = pathForRoute(route.page, route.meetingId)
      window.history.replaceState(null, '', `${nextPath}${window.location.search}`)
    }
    loadConfig()
    loadSessions()
    if (initialMeetingIdRef.current) {
      void openSession(initialMeetingIdRef.current, false)
    }
  }, [])

  useEffect(() => {
    function handlePopState() {
      const route = routeFromLocation()
      if (route.page === 'room' && route.meetingId && route.meetingId !== activeSessionIdRef.current) {
        void openSession(route.meetingId, false)
        return
      }
      setPage(route.page)
      if (route.page === 'history') void loadSessions()
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

  const recognizedSpeakers = useMemo(() => {
    const map = new Map<string, RecognizedSpeaker>()
    for (const message of messages.slice(speakerPanelStartIndex)) {
      const id = getSpeakerIdentity(message)
      const existing = map.get(id)
      const displayName = speakerNames[id] ?? (message.speaker_name === '未知说话人' ? '' : message.speaker_name)
      if (existing) {
        existing.count += 1
        existing.latestScore = message.speaker_score
        if (displayName) existing.name = displayName
      } else {
        map.set(id, {
          id,
          name: displayName,
          role: message.role,
          count: 1,
          latestScore: message.speaker_score,
        })
      }
    }
    return Array.from(map.values())
  }, [messages, speakerNames, speakerPanelStartIndex])

  const availableModelCards: ModelPreset[] = useMemo(() => [
    ...modelPresets,
    ...(config.custom_models || []).map((item) => ({
      id: item.id,
      name: item.name,
      model: item.model,
      baseUrl: item.base_url,
      apiType: item.api_type,
      description: item.description || '用户自定义模型',
      custom: true,
    })),
  ], [config.custom_models])

  function getSpeakerIdentity(message: Message) {
    return message.speaker_identity_id || message.participant_id || `voice_${message.id}`
  }

  async function loadSessions() {
    const res = await fetch(`${API_BASE}/api/sessions`)
    if (res.ok) setSessions(await res.json())
  }

  async function loadConfig() {
    const res = await fetch(`${API_BASE}/api/config`)
    if (res.ok) setConfig(await res.json())
  }

  function navigateTo(nextPage: Page, meetingId = sessionId) {
    setPage(nextPage)
    if (nextPage === 'history') void loadSessions()
    const nextPath = pathForRoute(nextPage, nextPage === 'room' ? meetingId : undefined)
    if (window.location.pathname !== nextPath) {
      window.history.pushState(null, '', nextPath)
    }
  }

  async function saveConfig() {
    const configToSave = ensureCurrentModelVisible(config)
    const res = await fetch(`${API_BASE}/api/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(configToSave),
    })
    if (!res.ok) {
      alert('模型配置保存失败')
      return
    }
    setConfig(await res.json())
    alert('模型配置已保存')
  }

  async function testModelConnection() {
    setTestingConnection(true)
    setConnectionTest('正在测试当前模型连接...')
    try {
      const res = await fetch(`${API_BASE}/api/config/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const data = await res.json()
      setConnectionTest(`${data.ok ? '✅ 连接成功' : '❌ 连接失败'}｜${data.provider} / ${data.model}｜${data.latency_ms}ms｜${data.message}`)
    } catch (error) {
      setConnectionTest(`❌ 连接测试异常：${String(error)}`)
    } finally {
      setTestingConnection(false)
    }
  }

  function applyModelPreset(presetId: string) {
    const preset = availableModelCards.find((item) => item.id === presetId)
    if (!preset) return
    setConfig((current) => ({
      ...current,
      llm_provider: preset.id,
      llm_model: preset.model,
      llm_base_url: preset.baseUrl,
      llm_api_type: preset.apiType,
      model_availability: {
        ...current.model_availability,
        [preset.id]: true,
      },
    }))
    setConnectionTest('')
  }

  function toggleModelAvailability(presetId: string, enabled: boolean) {
    setConfig((current) => ({
      ...current,
      model_availability: {
        ...current.model_availability,
        [presetId]: enabled,
      },
    }))
  }

  function normalizeModelId(value: string) {
    return value.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '')
  }

  function ensureCurrentModelVisible(current: ModelConfig): ModelConfig {
    const providerId = normalizeModelId(current.llm_provider || current.llm_model)
    if (!providerId || !current.llm_model.trim() || !current.llm_base_url.trim()) {
      return current
    }

    const presetExists = modelPresets.some((item) => item.id === providerId)
    const nextAvailability = {
      ...current.model_availability,
      [providerId]: true,
    }

    if (presetExists) {
      return {
        ...current,
        llm_provider: providerId,
        model_availability: nextAvailability,
      }
    }

    const nextCustomModel: CustomModelConfig = {
      id: providerId,
      name: current.llm_provider.trim() || providerId,
      model: current.llm_model.trim(),
      base_url: current.llm_base_url.trim().replace(/\/$/, ''),
      api_type: current.llm_api_type,
      description: `保存的模型配置：${current.llm_model.trim()}`,
    }

    const existingIndex = (current.custom_models || []).findIndex((item) => item.id === providerId)
    const nextCustomModels = [...(current.custom_models || [])]
    if (existingIndex >= 0) {
      nextCustomModels[existingIndex] = nextCustomModel
    } else {
      nextCustomModels.push(nextCustomModel)
    }

    return {
      ...current,
      llm_provider: providerId,
      llm_base_url: nextCustomModel.base_url,
      model_availability: nextAvailability,
      custom_models: nextCustomModels,
    }
  }

  function removeCustomModel(id: string) {
    setConfig((current) => {
      const nextAvailability = { ...current.model_availability }
      delete nextAvailability[id]
      const removingActive = current.llm_provider === id
      return {
        ...current,
        llm_provider: removingActive ? defaultConfig.llm_provider : current.llm_provider,
        llm_model: removingActive ? defaultConfig.llm_model : current.llm_model,
        llm_base_url: removingActive ? defaultConfig.llm_base_url : current.llm_base_url,
        llm_api_type: removingActive ? defaultConfig.llm_api_type : current.llm_api_type,
        model_availability: nextAvailability,
        custom_models: (current.custom_models || []).filter((item) => item.id !== id),
      }
    })
  }

  async function enterMeeting() {
    if (!customerName.trim()) {
      alert('请输入客户名称')
      return
    }
    if (!goal.trim()) {
      alert('请输入会晤目标')
      return
    }

    const res = await fetch(`${API_BASE}/api/meetings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_name: customerName.trim(), goal }),
    })
    const data: SessionDetail = await res.json()
    setSessionId(data.id)
    activeSessionIdRef.current = data.id
    setSessionTitle(data.title)
    setMessages([])
    setSpeakerNames({})
    setSpeakerPanelStartIndex(0)
    setSpeakerPushTargets({})
    setAiQas([])
    setAgentMemory(null)
    setLatestSuggestion('')
    setSummary('')
    navigateTo('room', data.id)
    await loadSessions()
    await loadAgentMemory(data.id)
  }

  async function openSession(id: string, updateUrl = true) {
    const res = await fetch(`${API_BASE}/api/sessions/${id}`)
    if (!res.ok) {
      alert('会议不存在')
      return
    }
    const data: SessionDetail = await res.json()
    setSessionId(data.id)
    activeSessionIdRef.current = data.id
    setSessionTitle(data.title)
    setGoal(data.goal)
    setMessages(data.messages || [])
    setSpeakerPanelStartIndex(0)
    setSummary(data.summary || '')
    setAiQas((data.messages || []).filter((m) => m.ai_suggestion).map((m) => ({
      id: m.id,
      speakerName: m.speaker_name,
      question: m.text,
      answer: m.ai_suggestion || '',
      status: 'done',
    })))
    setLatestSuggestion([...data.messages].reverse().find((m) => m.ai_suggestion)?.ai_suggestion || '')
    setSpeakerNames({})
    setSpeakerPushTargets({})
    if (updateUrl) {
      navigateTo('room', data.id)
    } else {
      setPage('room')
    }
    await loadAgentMemory(data.id)
  }

  async function startRecording() {
    if (!sessionId) {
      alert('请先新建会议并进入会议室')
      return
    }

    setModelLoadPhase('loading')
    setModelLoadNote('正在初始化麦克风、WebSocket 和本地模型...')
    activeSessionIdRef.current = sessionId
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (error) {
      console.error(error)
      setModelLoadPhase('stopped')
      setModelLoadNote('麦克风不可用，模型未启动')
      alert('无法访问麦克风，请检查浏览器麦克风权限。')
      return
    }
    const audioContext = new AudioContext()
    const source = audioContext.createMediaStreamSource(stream)
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 2048
    source.connect(analyser)

    audioContextRef.current = audioContext
    audioSourceRef.current = source
    analyserRef.current = analyser
    mediaStreamRef.current = stream
    chunksRef.current = []
    recordingRef.current = true
    speechActiveRef.current = false

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${wsProtocol}//${window.location.host}/api/meetings/${sessionId}/asr/funasr/stream`)
    ws.binaryType = 'arraybuffer'
    websocketRef.current = ws
    streamingReadyRef.current = false

    ws.onopen = () => {
      setModelLoadPhase('loading')
      setModelLoadNote('WebSocket 已连接，等待 FunASR streaming 就绪...')
      setRecordingStatus('FunASR 流式识别已连接，请开始讲话')
    }
    ws.onerror = () => {
      setRecordingStatus('FunASR WebSocket 连接异常')
      cleanupStreamingRecorder('流式识别连接异常，已停止录音')
    }
    ws.onclose = () => {
      if (recordingRef.current) {
        cleanupStreamingRecorder('流式识别连接已断开，已停止录音')
      }
    }
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'partial') {
        setRecordingStatus(`实时字幕：${displayTranscriptText(data.text)}`)
      } else if (data.type === 'final') {
        const message: Message = data.message
        setMessages((items) => [...items, message])
        setRecordingStatus(`识别完成：${message.speaker_name}`)
        if (message.ai_suggestion) setLatestSuggestion(message.ai_suggestion)
        void maybePushQuestionToAi(message, [...messages, message])
        void loadSessions()
        void loadAgentMemory(sessionId)
      } else if (data.type === 'error') {
        setRecordingStatus(`流式识别错误：${data.message}`)
      } else if (data.type === 'loading') {
        setModelLoadPhase('loading')
        setModelLoadNote(data.message || 'FunASR 模型加载中...')
        setRecordingStatus(data.message || 'FunASR 模型加载中...')
      } else if (data.type === 'ready') {
        streamingReadyRef.current = true
        setModelLoadPhase('running')
        setModelLoadNote('实时识别链路已运行；按配置启用的增强模型会在 final 阶段按需加载。')
        setRecordingStatus('FunASR 流式识别已就绪，请开始讲话')
      }
    }

    const processor = audioContext.createScriptProcessor(4096, 1, 1)
    audioProcessorRef.current = processor
    source.connect(processor)
    processor.connect(audioContext.destination)

    processor.onaudioprocess = (event) => {
      if (!recordingRef.current || websocketRef.current?.readyState !== WebSocket.OPEN) return
      const input = event.inputBuffer.getChannelData(0)
      handlePcmFrame(input, audioContext.sampleRate)
    }

    setRecording(true)
    setModelLoadNote('录音已启动，等待识别服务返回就绪状态...')
    setRecordingStatus('监听中：请开始讲话')
  }

  function handlePcmFrame(input: Float32Array, sampleRate: number) {
    if (!streamingReadyRef.current) return
    const rms = Math.sqrt(input.reduce((sum, value) => sum + value * value, 0) / input.length)
    const now = Date.now()
    const activeConfig = configRef.current
    const effectiveEnergyThreshold = Math.min(activeConfig.energy_threshold || VOICE_ENERGY_TRIGGER, VOICE_ENERGY_TRIGGER)
    setEnergyLevel(rms)

    if (rms >= effectiveEnergyThreshold) {
      lastVoiceAtRef.current = now
      if (!speechActiveRef.current) {
        speechActiveRef.current = true
        segmentStartedAtRef.current = now
        setRecordingStatus('正在流式识别语音')
      }
    }

    if (speechActiveRef.current) {
      const pcm16 = floatTo16BitPcm(downsampleBuffer(input, sampleRate, 16000))
      websocketRef.current?.send(pcm16)

      const silenceMs = now - lastVoiceAtRef.current
      const speechMs = now - segmentStartedAtRef.current
      if (silenceMs >= VOICE_GAP_SPLIT_MS && speechMs >= configRef.current.min_speech_duration_ms) {
        sendStreamingControl({ type: 'final' })
        speechActiveRef.current = false
        setRecordingStatus('语音段已提交，等待最终结果')
      }
    }
  }

  function downsampleBuffer(buffer: Float32Array, inputRate: number, outputRate: number) {
    if (outputRate === inputRate) return buffer
    const sampleRateRatio = inputRate / outputRate
    const newLength = Math.round(buffer.length / sampleRateRatio)
    const result = new Float32Array(newLength)
    let offsetResult = 0
    let offsetBuffer = 0
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio)
      let accum = 0
      let count = 0
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i += 1) {
        accum += buffer[i]
        count += 1
      }
      result[offsetResult] = accum / count
      offsetResult += 1
      offsetBuffer = nextOffsetBuffer
    }
    return result
  }

  function floatTo16BitPcm(input: Float32Array) {
    const output = new Int16Array(input.length)
    for (let i = 0; i < input.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, input[i]))
      output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
    }
    return output.buffer
  }

  function sendStreamingControl(payload: object) {
    const ws = websocketRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload))
    }
  }

  function cleanupStreamingRecorder(status = '已停止录音') {
    recordingRef.current = false
    speechActiveRef.current = false
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current)
    audioProcessorRef.current?.disconnect()
    audioSourceRef.current?.disconnect()
    void audioContextRef.current?.close().catch(() => undefined)
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop())
    websocketRef.current = null
    streamingReadyRef.current = false
    audioProcessorRef.current = null
    audioSourceRef.current = null
    mediaStreamRef.current = null
    analyserRef.current = null
    audioContextRef.current = null
    setEnergyLevel(0)
    setRecording(false)
    setModelLoadPhase('stopped')
    setModelLoadNote('录音已停止，模型链路未运行')
    setRecordingStatus(status)
  }

  function startSpeechSegment() {
    const stream = mediaStreamRef.current
    if (!stream || segmentRecorderRef.current) return

    chunksRef.current = []
    speechActiveRef.current = true
    setRecordingStatus('正在录制语音段')
    segmentStartedAtRef.current = Date.now()
    lastVoiceAtRef.current = Date.now()

    const recorder = new MediaRecorder(stream)
    segmentRecorderRef.current = recorder

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data)
    }

    recorder.onstop = async () => {
      const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' })
      chunksRef.current = []
      segmentRecorderRef.current = null
      speechActiveRef.current = false
      if (blob.size > 0) await uploadAudioSegment(blob)
      if (recordingRef.current) setRecordingStatus('监听中：等待下一段语音')
    }

    recorder.start(250)
  }

  function monitorEnergy() {
    const analyser = analyserRef.current
    if (!analyser || !recordingRef.current) return

    const buffer = new Float32Array(analyser.fftSize)
    analyser.getFloatTimeDomainData(buffer)
    const rms = Math.sqrt(buffer.reduce((sum, value) => sum + value * value, 0) / buffer.length)
    const now = Date.now()
    const activeConfig = configRef.current
    const effectiveEnergyThreshold = Math.min(activeConfig.energy_threshold || VOICE_ENERGY_TRIGGER, VOICE_ENERGY_TRIGGER)

    setEnergyLevel(rms)

    if (rms >= effectiveEnergyThreshold) {
      lastVoiceAtRef.current = now
      if (!speechActiveRef.current) {
        startSpeechSegment()
      }
    }

    if (speechActiveRef.current) {
      const silenceMs = now - lastVoiceAtRef.current
      const speechMs = now - segmentStartedAtRef.current
      if (
        (silenceMs >= VOICE_GAP_SPLIT_MS && speechMs >= activeConfig.min_speech_duration_ms)
      ) {
        flushSpeechSegment()
      }
    }

    animationFrameRef.current = requestAnimationFrame(monitorEnergy)
  }

  function flushSpeechSegment() {
    const recorder = segmentRecorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop()
    } else {
      speechActiveRef.current = false
      chunksRef.current = []
      segmentRecorderRef.current = null
    }
  }

  async function uploadAudioSegment(blob: Blob) {
    const activeSessionId = activeSessionIdRef.current || sessionId
    if (!activeSessionId) return

    const form = new FormData()
    form.append('session_id', activeSessionId)
    form.append('file', blob, 'speech.webm')
    try {
      setRecordingStatus('语音段上传中，正在识别...')
      const res = await fetch(`${API_BASE}/api/meetings/${activeSessionId}/audio`, { method: 'POST', body: form })
      if (!res.ok) {
        const error = await res.json().catch(() => null)
        setRecordingStatus(error?.detail || '音频识别失败')
        alert(error?.detail || '音频识别失败，请稍后重试。')
        return
      }
      const data = await res.json()
      const newMessages: Message[] = Array.isArray(data) ? data : [data]
      if (newMessages.length === 0) {
        setRecordingStatus('识别完成，但没有返回文本')
        return
      }
      setMessages((items) => [...items, ...newMessages])
      setRecordingStatus(`识别完成：${newMessages.length} 条文本`)
      const latestAi = [...newMessages].reverse().find((item) => item.ai_suggestion)?.ai_suggestion
      if (latestAi) setLatestSuggestion(latestAi)
      let runningHistory = [...messages]
      for (const message of newMessages) {
        runningHistory = [...runningHistory, message]
        void maybePushQuestionToAi(message, runningHistory)
      }
      await loadSessions()
      await loadAgentMemory(activeSessionId)
    } catch (error) {
      console.error(error)
      setRecordingStatus('音频上传或识别异常，请检查后端日志')
    }
  }

  function stopRecording() {
    recordingRef.current = false
    if (speechActiveRef.current) {
      sendStreamingControl({ type: 'final' })
      speechActiveRef.current = false
    }
    sendStreamingControl({ type: 'close' })
    websocketRef.current?.close()
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current)
    flushSpeechSegment()
    window.setTimeout(() => {
      cleanupStreamingRecorder('已停止录音')
    }, 300)
  }

  async function applySpeakerName(identityId: string) {
    const name = speakerNames[identityId]?.trim()
    if (!sessionId || !name) return

    const res = await fetch(`${API_BASE}/api/meetings/${sessionId}/speakers/${identityId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speaker_name: name, role: 'customer', need_reply: true }),
    })
    if (!res.ok) {
      alert('关联参会人员失败')
      return
    }

    setMessages((items) => items.map((item) => (
      getSpeakerIdentity(item) === identityId
        ? { ...item, speaker_identity_id: identityId, speaker_name: name, role: 'customer', need_reply: true, corrected: true }
        : item
    )))
    await loadAgentMemory(sessionId)
  }

  async function loadAgentMemory(meetingId = sessionId) {
    if (!meetingId) return
    const res = await fetch(`${API_BASE}/api/meetings/${meetingId}/agent/memory`)
    if (res.ok) setAgentMemory(await res.json())
  }

  async function runAgent() {
    if (!sessionId) return
    const res = await fetch(`${API_BASE}/api/meetings/${sessionId}/agent/run`, { method: 'POST' })
    if (!res.ok) {
      alert('Agent 运行失败')
      return
    }
    const data = await res.json()
    setAgentMemory(data.memory)
  }

  function toggleSpeakerAiPush(identityId: string, enabled: boolean) {
    setSpeakerPushTargets((current) => ({ ...current, [identityId]: enabled }))
  }

  function clearRecognizedSpeakers() {
    setSpeakerNames({})
    setSpeakerPushTargets({})
    setSpeakerPanelStartIndex(messages.length)
  }

  async function clearRealtimeConversation() {
    if (!sessionId) return
    if (!confirm('确认清空当前会议的实时会话记录吗？')) return
    const res = await fetch(`${API_BASE}/api/meetings/${sessionId}/messages`, { method: 'DELETE' })
    if (!res.ok) {
      alert('清空实时会话失败')
      return
    }
    setMessages([])
    setSpeakerNames({})
    setSpeakerPushTargets({})
    setSpeakerPanelStartIndex(0)
    setAiQas([])
    setLatestSuggestion('')
    setSummary('')
    await loadAgentMemory(sessionId)
    await loadSessions()
  }

  function isLikelyQuestion(text: string) {
    const value = text.trim()
    if (!value) return false
    if (/[?？]$/.test(value) || /[?？]/.test(value)) return true
    return /(吗|呢|什么|为什么|为何|怎么|如何|是否|能否|能不能|可不可以|有没有|哪[个些里]|多少|几|何时|什么时候|请问)/.test(value)
  }

  async function maybePushQuestionToAi(message: Message, historyMessages: Message[]) {
    const identityId = getSpeakerIdentity(message)
    if (!speakerPushTargets[identityId]) return
    if (!isLikelyQuestion(message.text)) return

    const qaId = `qa_${message.id}`
    const speakerName = speakerNames[identityId] || message.speaker_name || identityId
    setAiQas((items) => [...items, {
      id: qaId,
      speakerName,
      question: message.text,
      answer: 'AI 正在思考...',
      status: 'thinking',
    }])

    const conversationHistory = historyMessages
      .slice(-8)
      .map((item) => `${item.speaker_name}: ${item.text}`)
      .join('\n')

    try {
      const res = await fetch(`${API_BASE}/api/ai/suggest-reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          meeting_goal: goal,
          conversation_history: conversationHistory,
          latest_customer_message: message.text,
        }),
      })
      if (!res.ok) {
        const error = await res.json().catch(() => null)
        throw new Error(error?.detail || 'AI 回复生成失败')
      }
      const data = await res.json()
      setLatestSuggestion(data.suggestion)
      setAiQas((items) => items.map((item) => item.id === qaId ? {
        ...item,
        answer: data.suggestion,
        status: 'done',
      } : item))
    } catch (error) {
      setAiQas((items) => items.map((item) => item.id === qaId ? {
        ...item,
        answer: `生成失败：${String(error)}`,
        status: 'error',
      } : item))
    }
  }

  async function summarizeSession() {
    if (!sessionId) return
    const res = await fetch(`${API_BASE}/api/meetings/${sessionId}/summary`, { method: 'POST' })
    if (!res.ok) {
      const error = await res.json().catch(() => null)
      alert(error?.detail || '生成会议总结失败')
      return
    }
    const data = await res.json()
    setSummary(data.summary)
    await loadSessions()
  }

  async function exportSession() {
    if (!sessionId) return
    const res = await fetch(`${API_BASE}/api/meetings/${sessionId}/export`, { method: 'POST' })
    const data = await res.json()
    alert(`已导出：${data.file}`)
  }

  function updateConfig<K extends keyof ModelConfig>(key: K, value: ModelConfig[K]) {
    setConfig((current) => ({ ...current, [key]: value }))
  }

  function phaseFor(enabled: boolean): ModelLoadPhase {
    return enabled ? modelLoadPhase : 'stopped'
  }

  function renderLoadItem(name: string, phase: ModelLoadPhase, detail: string) {
    const labels: Record<ModelLoadPhase, string> = {
      stopped: '停止',
      loading: '加载中',
      running: '运行',
    }
    return (
      <div className="load-progress-item">
        <div>
          <strong>{name}</strong>
          <span>{detail}</span>
        </div>
        <em className={`load-status ${phase}`}>{labels[phase]}</em>
      </div>
    )
  }

  function renderLoadProgress() {
    const asrPhase = phaseFor(recording || modelLoadPhase === 'loading')
    const punctuationPhase = phaseFor(config.punctuation_provider === 'funasr_punc' && (recording || modelLoadPhase === 'loading'))
    const bilingualPhase = phaseFor(config.bilingual_asr_enabled && (recording || modelLoadPhase === 'loading'))
    const speakerPhase = phaseFor(recording || modelLoadPhase === 'loading')
    const diarizationPhase = phaseFor(config.speaker_diarization_enabled && (recording || modelLoadPhase === 'loading'))
    const llmPhase = phaseFor(recording || modelLoadPhase === 'loading')

    return (
      <section className="page-card load-progress-section">
        <div className="section-heading">
          <div>
            <h2>加载进度</h2>
            <p className="muted">{modelLoadNote}</p>
          </div>
          <div className="section-actions">
            <button className="secondary-action" onClick={() => setLoadProgressOpen((open) => !open)}>
              {loadProgressOpen ? '折叠' : '展开'}
            </button>
          </div>
        </div>
        {loadProgressOpen && (
          <div className="load-progress-columns">
            <div className="load-progress-card">
              <h3>语音识别链路</h3>
              {renderLoadItem('FunASR Streaming', asrPhase, config.funasr_model)}
              {renderLoadItem('Final 标点恢复', punctuationPhase, config.punctuation_provider === 'funasr_punc' ? config.punctuation_model : config.punctuation_provider)}
              {renderLoadItem('双语识别增强', bilingualPhase, config.bilingual_asr_enabled ? `faster-whisper ${config.whisper_model}` : '未开启')}
            </div>
            <div className="load-progress-card">
              <h3>说话人 / AI 链路</h3>
              {renderLoadItem('SpeechBrain ECAPA', speakerPhase, '声纹匹配与未知说话人聚类')}
              {renderLoadItem('cam++ 说话人分离', diarizationPhase, config.speaker_diarization_enabled ? 'FunASR campplus' : '未开启')}
              {renderLoadItem('AI 回复服务', llmPhase, config.llm_provider ? `${config.llm_provider} / ${config.llm_model}` : '未配置')}
            </div>
          </div>
        )}
      </section>
    )
  }

  return (
    <div className={menuCollapsed ? 'app-shell menu-collapsed' : 'app-shell'}>
      <aside className="sidebar">
        <div className="sidebar-top">
          <div className="brand">
            <strong>{menuCollapsed ? 'AI' : 'AI 会谈辅助'}</strong>
            {!menuCollapsed && <span>Meeting Copilot</span>}
          </div>
          <button
            className="collapse-button"
            title={menuCollapsed ? '展开菜单' : '折叠菜单'}
            aria-label={menuCollapsed ? '展开菜单' : '折叠菜单'}
            onClick={() => setMenuCollapsed((collapsed) => !collapsed)}
          >
            {menuCollapsed ? '›' : '‹'}
          </button>
        </div>
        <nav className="main-menu">
          <button title="新建会议" className={page === 'new' ? 'active' : ''} onClick={() => navigateTo('new')}><span>＋</span><b>新建会议</b></button>
          <button title="历史会议" className={page === 'history' ? 'active' : ''} onClick={() => navigateTo('history')}><span>↺</span><b>历史会议</b></button>
          <div className={page === 'modelSettings' || page === 'asrSettings' || page === 'voiceSettings' || page === 'themeSettings' ? 'menu-group active' : 'menu-group'}>
            <div className="menu-parent" title="系统配置"><span>⚙</span><b>系统配置</b></div>
            <div className="submenu">
              <button title="LLM模型配置" className={page === 'modelSettings' ? 'active' : ''} onClick={() => navigateTo('modelSettings')}><span>◦</span><b>LLM模型配置</b></button>
              <button title="ASR 配置" className={page === 'asrSettings' ? 'active' : ''} onClick={() => navigateTo('asrSettings')}><span>◦</span><b>ASR 配置</b></button>
              <button title="语音识别参数" className={page === 'voiceSettings' ? 'active' : ''} onClick={() => navigateTo('voiceSettings')}><span>◦</span><b>语音识别参数</b></button>
              <button title="主题风格" className={page === 'themeSettings' ? 'active' : ''} onClick={() => navigateTo('themeSettings')}><span>◦</span><b>主题风格</b></button>
            </div>
          </div>
        </nav>
        {sessionId && (
          <button title="返回会议室" className={page === 'room' ? 'active room-entry' : 'room-entry'} onClick={() => navigateTo('room')}>
            <span>↗</span><b>返回会议室</b>
          </button>
        )}
      </aside>

      <main className="content">
        {page === 'new' && renderNewMeeting()}
        {page === 'history' && renderHistory()}
        {page === 'modelSettings' && renderModelSettings()}
        {page === 'asrSettings' && renderAsrSettings()}
        {page === 'voiceSettings' && renderVoiceSettings()}
        {page === 'themeSettings' && renderThemeSettings()}
        {page === 'room' && renderMeetingRoom()}
      </main>
    </div>
  )

  function renderNewMeeting() {
    return (
      <section className="page-card narrow-card">
        <div className="page-title">
          <h1>新建会议</h1>
          <p>只需填写客户名称和会晤目标，会议时间戳由系统后台自动记录。</p>
        </div>
        <label className="field">
          客户名称
          <input placeholder="例如：上海某某科技有限公司" value={customerName} onChange={(e) => setCustomerName(e.target.value)} />
        </label>
        <label className="field">
          会晤目标
          <textarea value={goal} onChange={(e) => setGoal(e.target.value)} />
        </label>
        <button className="primary-action" onClick={enterMeeting}>进入会议</button>
      </section>
    )
  }

  function renderHistory() {
    return (
      <section className="page-card">
        <div className="page-title row-between">
          <div>
            <h1>历史会议</h1>
            <p>查看本地保存的会议记录，并可重新进入会议室查看或继续导出。</p>
          </div>
          <button onClick={loadSessions}>刷新</button>
        </div>
        <div className="history-list">
          {sessions.length === 0 && <div className="empty">暂无历史会议</div>}
          {sessions.map((session) => (
            <button className="history-item" key={session.id} onClick={() => openSession(session.id)}>
              <strong>{session.title}</strong>
              <span>{session.goal || '未填写会晤目标'}</span>
              <small>{session.started_at} · {session.message_count} 条记录 · {session.has_summary ? '已总结' : '未总结'}</small>
            </button>
          ))}
        </div>
      </section>
    )
  }

  function renderModelSettings() {
    return (
      <div className="settings-stack">
        <section className="page-card">
          <div className="page-title">
            <h1>LLM模型配置</h1>
            <p>管理 Grok、Qwen、GPT 等 LLM 模型可用性，测试连接，并配置当前会议助手使用的大模型。</p>
          </div>

          <div className="model-section-title">
            <h2>模型可用性</h2>
            <span>启用后表示该模型可被系统选择；点击“套用”会将其设为当前 LLM。</span>
          </div>
          <div className="model-provider-grid">
            {availableModelCards.map((preset) => {
              const enabled = config.model_availability?.[preset.id] ?? false
              const active = config.llm_provider === preset.id
              return (
                <div className={active ? 'model-card active' : 'model-card'} key={preset.id}>
                  <div className="model-card-header">
                    <div>
                      <strong>{preset.name}</strong>
                      <small>{preset.model}</small>
                    </div>
                    <label className="switch-label">
                      <input
                        type="checkbox"
                        checked={enabled}
                        onChange={(e) => toggleModelAvailability(preset.id, e.target.checked)}
                      />
                      可用
                    </label>
                  </div>
                  <p>{preset.description}</p>
                  <code>{preset.baseUrl}</code>
                  <div className="model-card-actions">
                    <button className="secondary-action" onClick={() => applyModelPreset(preset.id)}>套用</button>
                    {preset.custom && <button className="danger subtle-danger" onClick={() => removeCustomModel(preset.id)}>删除</button>}
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        <section className="page-card">
          <div className="model-section-title">
            <h2>当前 LLM 连接</h2>
            <span>展示当前会被会议助手用于 AI 回复、总结和连接测试的 LLM。</span>
          </div>
          <div className="connection-summary">
            <div>
              <span>Provider</span>
              <strong>{config.llm_provider || '-'}</strong>
            </div>
            <div>
              <span>Model</span>
              <strong>{config.llm_model || '-'}</strong>
            </div>
            <div>
              <span>接口类型</span>
              <strong>{config.llm_api_type === 'responses' ? 'Responses' : 'Chat Completions'}</strong>
            </div>
            <div>
              <span>Base URL</span>
              <code>{config.llm_base_url || '-'}</code>
            </div>
            <div>
              <span>API Key</span>
              <strong>{config.llm_api_key ? '已配置' : '未配置'}</strong>
            </div>
            <div>
              <span>请求超时</span>
              <strong>{config.llm_timeout_seconds}s</strong>
            </div>
          </div>
          <div className="model-actions">
            <button className="secondary-action" disabled={testingConnection} onClick={testModelConnection}>
              {testingConnection ? '测试中...' : '测试当前 LLM 连接'}
            </button>
          </div>
          {connectionTest && <div className="connection-result">{connectionTest}</div>}
        </section>

        <section className="page-card">
          <div className="model-section-title">
            <h2>LLM 连接配置</h2>
            <span>修改 Provider、模型、Base URL、接口类型和 API Key；保存后会同步到“当前 LLM 连接”和“模型可用性”。</span>
          </div>
          <div className="config-grid">
            <label>LLM Provider<input value={config.llm_provider} onChange={(e) => updateConfig('llm_provider', e.target.value)} /></label>
            <label>LLM Model<input value={config.llm_model} onChange={(e) => updateConfig('llm_model', e.target.value)} /></label>
            <label>Base URL<input value={config.llm_base_url} onChange={(e) => updateConfig('llm_base_url', e.target.value)} /></label>
            <label>接口类型
              <select value={config.llm_api_type} onChange={(e) => updateConfig('llm_api_type', e.target.value as ModelConfig['llm_api_type'])}>
                <option value="chat_completions">Chat Completions (/v1/chat/completions)</option>
                <option value="responses">Responses (/v1/responses)</option>
              </select>
            </label>
            <label>API Key<input type="password" value={config.llm_api_key} placeholder="未配置" title="已保存的密钥以掩码显示；如需更换请直接输入新值，留空并保存可清除" onChange={(e) => updateConfig('llm_api_key', e.target.value)} /></label>
            <small className="field-hint">为安全起见，已保存的密钥仅以掩码返回；不修改直接保存会保留原密钥。</small>
            <label>请求超时秒数<input type="number" value={config.llm_timeout_seconds} onChange={(e) => updateConfig('llm_timeout_seconds', Number(e.target.value))} /></label>
          </div>

          <div className="model-actions">
            <button onClick={saveConfig}>保存 LLM 模型配置</button>
          </div>
        </section>
      </div>
    )
  }

  function renderAsrSettings() {
    return (
      <section className="page-card">
        <div className="page-title">
          <h1>ASR Provider 配置</h1>
          <p>配置语音识别后端、FunASR 模型、双语增强、标点恢复和 OCI Speech 参数。</p>
        </div>
        <div className="config-grid">
          <label>ASR Provider
            <select value={config.asr_provider} onChange={(e) => updateConfig('asr_provider', e.target.value as ModelConfig['asr_provider'])}>
              <option value="funasr_streaming">FunASR Streaming（本地流式）</option>
              <option value="faster_whisper">faster-whisper（本地短音频）</option>
              <option value="oci_speech">OCI Speech（云端实时，待接入）</option>
            </select>
          </label>
          <label>FunASR Model<input value={config.funasr_model} onChange={(e) => updateConfig('funasr_model', e.target.value)} /></label>
          <label>FunASR Device<input value={config.funasr_device} onChange={(e) => updateConfig('funasr_device', e.target.value)} /></label>
          <label>双语识别增强
            <select value={config.bilingual_asr_enabled ? 'on' : 'off'} onChange={(e) => updateConfig('bilingual_asr_enabled', e.target.value === 'on')}>
              <option value="off">关闭（默认，低延迟）</option>
              <option value="on">开启（final 使用 faster-whisper 自动识别中英文）</option>
            </select>
          </label>
          <label>标点 Provider
            <select value={config.punctuation_provider} onChange={(e) => updateConfig('punctuation_provider', e.target.value as ModelConfig['punctuation_provider'])}>
              <option value="funasr_punc">FunASR / HuggingFace 标点模型（实时 final 推荐）</option>
              <option value="asr">ASR / Whisper 原始标点（FunASR streaming 通常无标点）</option>
              <option value="rule">规则标点</option>
              <option value="off">关闭标点恢复</option>
            </select>
          </label>
          <label>标点模型<input value={config.punctuation_model} onChange={(e) => updateConfig('punctuation_model', e.target.value)} /></label>
          <label>标点设备<input value={config.punctuation_device} onChange={(e) => updateConfig('punctuation_device', e.target.value)} /></label>
        </div>
        <details className="advanced-settings">
          <summary>
            <span>OCI Speech 配置</span>
            <small>云端 ASR 预留参数，当前本地实时识别不需要配置</small>
          </summary>
          <div className="config-grid">
            <label>OCI Region<input placeholder="us-ashburn-1" value={config.oci_speech_region} onChange={(e) => updateConfig('oci_speech_region', e.target.value)} /></label>
            <label>OCI Compartment OCID<input value={config.oci_speech_compartment_id} onChange={(e) => updateConfig('oci_speech_compartment_id', e.target.value)} /></label>
            <label>OCI Language<input value={config.oci_speech_language} onChange={(e) => updateConfig('oci_speech_language', e.target.value)} /></label>
            <label>OCI Profile<input value={config.oci_speech_profile} onChange={(e) => updateConfig('oci_speech_profile', e.target.value)} /></label>
            <label>OCI Config File<input value={config.oci_speech_config_file} onChange={(e) => updateConfig('oci_speech_config_file', e.target.value)} /></label>
            <label>OCI Realtime Endpoint<input placeholder="wss://realtime.aiservice.<region>.oci.oraclecloud.com/..." value={config.oci_speech_realtime_endpoint} onChange={(e) => updateConfig('oci_speech_realtime_endpoint', e.target.value)} /></label>
          </div>
        </details>
        <div className="model-actions">
          <button onClick={saveConfig}>保存 ASR 配置</button>
        </div>
      </section>
    )
  }

  function renderVoiceSettings() {
    return (
      <section className="page-card">
        <div className="page-title">
          <h1>本地语音与识别参数</h1>
          <p>配置 faster-whisper 短音频 fallback、声纹匹配、cam++ 说话人分离和浏览器能量切分。实时识别主链路使用 FunASR streaming。</p>
        </div>
        <div className="config-grid">
          <label>faster-whisper fallback 模型<input value={config.whisper_model} onChange={(e) => updateConfig('whisper_model', e.target.value)} /></label>
          <label>faster-whisper fallback 设备<input value={config.whisper_device} onChange={(e) => updateConfig('whisper_device', e.target.value)} /></label>
          <label>faster-whisper fallback Compute Type<input value={config.whisper_compute_type} onChange={(e) => updateConfig('whisper_compute_type', e.target.value)} /></label>
          <label>已知参会人匹配阈值<input type="number" step="0.01" min="0" max="1" value={config.speaker_threshold} onChange={(e) => updateConfig('speaker_threshold', Number(e.target.value))} /></label>
          <label>未知说话人聚类阈值<input type="number" step="0.01" min="0" max="1" value={config.speaker_cluster_threshold} onChange={(e) => updateConfig('speaker_cluster_threshold', Number(e.target.value))} /></label>
          <label>cam++ 说话人分离
            <select value={config.speaker_diarization_enabled ? 'on' : 'off'} onChange={(e) => updateConfig('speaker_diarization_enabled', e.target.value === 'on')}>
              <option value="off">关闭（推荐，本地轻量）</option>
              <option value="on">开启（需要 Colima 约 12GB 内存）</option>
            </select>
          </label>
          <label>能量阈值<input type="number" step="0.001" value={config.energy_threshold} onChange={(e) => updateConfig('energy_threshold', Number(e.target.value))} /></label>
          <label>语音间断切分 ms<input type="number" value={VOICE_GAP_SPLIT_MS} disabled readOnly /></label>
          <label>最短语音 ms<input type="number" value={config.min_speech_duration_ms} onChange={(e) => updateConfig('min_speech_duration_ms', Number(e.target.value))} /></label>
        </div>
        <div className="model-actions">
          <button onClick={saveConfig}>保存语音参数</button>
        </div>
      </section>
    )
  }

  function renderThemeSettings() {
    return (
      <section className="page-card">
        <div className="page-title">
          <h1>系统主题风格</h1>
          <p>切换本地页面的显示风格，不影响后端数据。</p>
        </div>
        <div className="theme-options">
          <button className={theme === 'dark' ? 'active' : ''} onClick={() => setTheme('dark')}>深色商务</button>
          <button className={theme === 'light' ? 'active' : ''} onClick={() => setTheme('light')}>浅色简洁</button>
          <button className={theme === 'blue' ? 'active' : ''} onClick={() => setTheme('blue')}>蓝色科技</button>
        </div>
      </section>
    )
  }

  function renderMeetingRoom() {
    return (
      <div className="meeting-room">
        <section className="page-card room-header">
          <div>
            <h1>{sessionTitle || '会议室'}</h1>
            <p className="muted">当前会话：{sessionId || '-'}</p>
          </div>
          <div className="room-actions">
            {!recording ? <button onClick={startRecording}>开始录音</button> : <button className="danger" onClick={stopRecording}>停止录音</button>}
            <button onClick={exportSession}>导出会议</button>
          </div>
        </section>

        {renderLoadProgress()}

        <section className="page-card speaker-panel">
          <div className="section-heading">
            <h2>参会人员</h2>
            <div className="section-actions">
              <button className="secondary-action" onClick={clearRecognizedSpeakers}>清空</button>
              <button className="secondary-action" onClick={() => setSpeakerPanelOpen((open) => !open)}>
                {speakerPanelOpen ? '折叠' : '展开'}
              </button>
            </div>
          </div>
          {speakerPanelOpen && (
            <div className="speaker-list">
              {recognizedSpeakers.length === 0 && <div className="empty">开始录音后，系统会在这里动态添加识别到的个体 ID。</div>}
              {recognizedSpeakers.map((speaker) => (
                <div className="speaker-row" key={speaker.id}>
                  <code>{speaker.id}</code>
                  <input
                    placeholder="输入参会人员名称并回车关联"
                    value={speakerNames[speaker.id] ?? speaker.name}
                    onChange={(e) => setSpeakerNames((current) => ({ ...current, [speaker.id]: e.target.value }))}
                    onBlur={() => applySpeakerName(speaker.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void applySpeakerName(speaker.id)
                    }}
                  />
                  <label className="speaker-push-toggle">
                    <input
                      type="checkbox"
                      checked={speakerPushTargets[speaker.id] ?? false}
                      onChange={(e) => toggleSpeakerAiPush(speaker.id, e.target.checked)}
                    />
                    推送 AI
                  </label>
                  <span>{speaker.count} 条发言</span>
                  <span>匹配分：{speaker.latestScore?.toFixed(2) ?? '-'}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <div className="room-columns">
          <section className="page-card conversation-card">
            <div className="section-heading">
              <h2>实时会话</h2>
              <div className="section-actions">
                <span>当前能量：{energyLevel.toFixed(4)} / 触发阈值：{Math.min(config.energy_threshold || VOICE_ENERGY_TRIGGER, VOICE_ENERGY_TRIGGER)} / 停顿 {VOICE_GAP_SPLIT_MS / 1000}s 切分</span>
                <button type="button" className="secondary-action conversation-clear-action" onClick={clearRealtimeConversation}>清空</button>
              </div>
            </div>
            <div className="recording-status">{recordingStatus}</div>
            <div className="messages">
              {messages.length === 0 && <div className="empty">暂无会话内容</div>}
              {messages.map((message) => (
                <div className={`message ${message.role}`} key={message.id}>
                  <div className="message-header">
                    <strong>{message.speaker_name}{message.corrected ? '（已修正）' : ''}</strong>
                    <code>{getSpeakerIdentity(message)}</code>
                    {typeof message.start_time === 'number' && typeof message.end_time === 'number' && <span className="score">{message.start_time.toFixed(1)}s - {message.end_time.toFixed(1)}s</span>}
                    <span className="score">识别时间：{formatMessageTime(message.created_at)}</span>
                    <span className="score">匹配分：{message.speaker_score?.toFixed(2) ?? '-'}</span>
                  </div>
                  <p>{message.text}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="page-card ai-card">
            <h2>AI 回复</h2>
            <div className="qa-panel">
              {aiQas.length === 0 && (
                <div className="empty qa-empty">
                  勾选“参会人员”中的“推送 AI”后，该参会人的问题会自动进入这里生成回复。
                </div>
              )}
              {aiQas.map((item) => (
                <div className={`qa-item ${item.status}`} key={item.id}>
                  <div className="qa-question">
                    <span>{item.speakerName}</span>
                    <p>{item.question}</p>
                  </div>
                  <div className="qa-answer">
                    <span>AI 回复</span>
                    <p>{item.answer}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <section className="page-card agent-card">
          <div className="section-heading">
            <h2>Agent 工作台</h2>
            <button className="secondary-action" onClick={runAgent}>刷新 Agent 记忆</button>
          </div>
          {!agentMemory ? (
            <div className="empty">暂无 Agent 记忆。录音产生会话后，Agent 会自动维护上下文。</div>
          ) : (
            <div className="agent-grid">
              <AgentMemoryBlock title="短期上下文" items={agentMemory.short_term_context} />
              <AgentMemoryBlock title="客户痛点" items={agentMemory.pain_points} />
              <AgentMemoryBlock title="风险/异议" items={agentMemory.risks} />
              <AgentMemoryBlock title="承诺事项" items={agentMemory.commitments} />
              <AgentMemoryBlock title="后续待办" items={agentMemory.todos} />
              <AgentMemoryBlock title="关键问题" items={agentMemory.questions} />
              <div className="agent-memory-block decisions-block">
                <h3>决策日志</h3>
                {agentMemory.decisions.length === 0 && <p className="empty">暂无决策日志</p>}
                {agentMemory.decisions.slice(-8).reverse().map((decision) => (
                  <div className="decision-item" key={decision.id}>
                    <strong>{decision.type}</strong>
                    <span>{decision.reason}</span>
                    <p>{decision.speaker_name}: {decision.content}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        <section className="page-card summary-card">
          <div className="section-heading">
            <h2>会议总结</h2>
            <button onClick={summarizeSession}>生成会议总结</button>
          </div>
          <p>{summary || '会议总结默认不自动生成。请在会议结束后人工点击按钮触发。'}</p>
        </section>
      </div>
    )
  }
}
