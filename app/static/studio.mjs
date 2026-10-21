import {
  MODELS, DEFAULT_CONFIG, SAMPLES, StudioError, apiRequest, audioContentType,
  buildRequest, characterCount, formatBytes, formatMilliseconds, modelStates,
  normalizeConfig, serializableResult, validateResult,
} from './studio-core.mjs';

const $ = (id) => document.getElementById(id);
const state = {
  config: { ...DEFAULT_CONFIG }, configLoaded: false, key: '', authorized: false,
  models: modelStates([], {}, DEFAULT_CONFIG, false), selected: 'sentiment', task: 'sentiment',
  busy: false, request: null, result: null, audioUrl: null, runs: [], view: 'playground',
  baselineReady: false, backendAlive: false, refreshPromise: null,
};
const text = (id, value) => { $(id).textContent = value; };
const show = (id, visible) => { $(id).hidden = !visible; };
const selectedModel = () => state.models.find((model) => model.id === state.selected);
let toastTimer;
function toast(message) {
  text('toast', message); show('toast', true); $('toast').dataset.visible = 'true';
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('toast').dataset.visible = 'false'; show('toast', false); }, 3500);
}
function showError(message) { text('error-message', message); show('error-banner', true); }
function clearError() { show('error-banner', false); text('error-message', ''); }
function element(tag, className, value) {
  const node = document.createElement(tag); if (className) node.className = className;
  if (value !== undefined) node.textContent = value; return node;
}
function statusLabel(value) {
  return { ready: 'Ready', loading: 'Loading', disabled: 'Not enabled', unavailable: 'Unavailable', locked: 'Key required' }[value] || 'Checking';
}

function updateInput() {
  const model = selectedModel();
  const count = characterCount($('input-text').value);
  text('char-count', `${count.toLocaleString()} / ${model.maxChars.toLocaleString()}`);
  $('char-count').dataset.over = String(count > model.maxChars);
  const limit = Math.min(state.config.transcription_max_bytes, state.config.max_request_bytes);
  text('model-limit', model.task === 'transcribe' ? `${formatBytes(limit)} audio limit` : `${model.maxChars.toLocaleString()} character limit`);
  text('input-hint', model.task === 'sentiment'
    ? 'English text · binary positive / negative classification'
    : model.task === 'generate' ? 'Describe what you would like the model to create.'
      : model.task === 'speech' ? 'Write the words you would like to hear.' : `Raw audio upload · up to ${formatBytes(limit)}`);
  const file = $('audio-file').files[0];
  text('audio-file-name', file ? `${file.name} · ${formatBytes(file.size)}` : 'No audio file selected');
  const validInput = model.task === 'transcribe'
    ? file && file.size > 0 && file.size <= limit && audioContentType(file)
    : count > 0 && $('input-text').value.trim() && count <= model.maxChars;
  const tokens = Number($('max-output-tokens').value);
  const validOptions = model.task !== 'generate' || Number.isInteger(tokens) && tokens >= 1 && tokens <= state.config.generation_max_output_tokens;
  const consent = model.provider !== 'groq' || $('provider-consent').checked;
  $('run-button').disabled = state.busy || !state.configLoaded || !state.authorized || model.state !== 'ready' || !validInput || !validOptions || !consent;
  text('run-label', state.busy ? 'Running model…' : { sentiment: 'Analyze sentiment', generate: 'Generate text', speech: 'Create speech', transcribe: 'Transcribe audio' }[model.task]);
  $('run-form').dataset.busy = String(state.busy);
  $('run-button').dataset.busy = String(state.busy);
  $('run-form').setAttribute('aria-busy', String(state.busy));
  show('cancel-run', state.busy);
  $('model-select').disabled = state.busy;
  document.querySelectorAll('#task-tabs button').forEach((button) => { button.disabled = state.busy; });
}

function renderModel() {
  const model = selectedModel();
  const visibleModels = state.models.filter((item) => item.task === state.task);
  $('model-select').replaceChildren(...visibleModels.map((item) => {
    const option = element('option', null, `${item.name} · ${item.subtitle} — ${statusLabel(state.authorized ? item.state : 'locked')}`);
    option.value = item.id; return option;
  }));
  $('model-select').value = model.id;
  text('model-name', model.name); text('model-description', model.description); text('model-runtime', model.runtime);
  const availability = state.authorized ? model.state : state.config.auth_required ? 'locked' : 'unavailable';
  text('model-state', statusLabel(availability)); $('model-state').dataset.state = availability;
  document.querySelectorAll('#task-tabs button').forEach((button) => {
    const selected = button.dataset.task === model.task;
    button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
  });
  $('run-form').setAttribute('aria-labelledby', `task-${model.task}`);
  show('text-input-group', model.task !== 'transcribe'); show('audio-input-group', model.task === 'transcribe');
  show('generation-options', model.task === 'generate'); show('speech-options', model.task === 'speech');
  show('provider-notice', model.provider === 'groq'); show('examples', model.task !== 'transcribe');
  text('input-label', model.task === 'speech' ? 'Speech script' : model.task === 'generate' ? 'Your prompt' : 'Your input');
  $('input-text').placeholder = model.task === 'sentiment' ? 'Paste a review, a comment, or a little honest feedback…'
    : model.task === 'generate' ? 'Explain a concept, write something new, or explore an idea…' : 'Write a short message to turn into speech…';
  $('max-output-tokens').max = state.config.generation_max_output_tokens;
  if (Number($('max-output-tokens').value) > state.config.generation_max_output_tokens) $('max-output-tokens').value = Math.min(128, state.config.generation_max_output_tokens);
  const priorVoice = $('voice-select').value;
  const defaultOption = element('option', null, 'Model default'); defaultOption.value = '';
  $('voice-select').replaceChildren(defaultOption, ...model.voices.map((voice) => {
    const option = element('option', null, voice); option.value = voice; return option;
  }));
  if (model.voices.includes(priorVoice)) $('voice-select').value = priorVoice;
  const examples = SAMPLES[model.task] || [];
  document.querySelectorAll('#examples button').forEach((button, index) => {
    button.textContent = examples[index]?.[0] || ''; button.hidden = !examples[index]; button.dataset.exampleIndex = String(index);
  });
  const notice = availability === 'ready'
    ? 'Connected to the real model. Results appear only after a successful API response.'
    : availability === 'locked' ? 'Add the server API key using Session access. Keys stay in memory for this tab.'
      : availability === 'disabled' ? 'This optional adapter is not enabled on the server. No weights are downloaded and no provider request will be sent.'
        : availability === 'loading' ? 'The adapter is loading. Refresh status once it is ready; inference is not simulated.'
          : 'The model is not available. Check server readiness or ask the operator to inspect its configuration.';
  text('status-details', notice);
  updateInput();
}

function renderCatalog() {
  const ready = state.authorized ? state.models.filter((model) => model.state === 'ready').length : 0;
  text('catalog-summary', state.authorized ? `${ready} ready · ${state.models.length} configured adapters` : 'Unlock session access to see live model availability.');
  $('catalog-grid').replaceChildren(...state.models.map((model) => {
    const card = element('article', 'catalog-card');
    const head = element('div', 'catalog-card-head');
    const icon = element('span', 'catalog-icon', { sentiment: 'Aa', generate: '✦', speech: '♫', transcribe: '≋' }[model.task]);
    icon.setAttribute('aria-hidden', 'true');
    const badge = element('span', 'model-state', statusLabel(state.authorized ? model.state : 'locked'));
    badge.dataset.state = state.authorized ? model.state : 'locked'; head.append(icon, badge);
    card.append(head, element('h3', null, model.name), element('p', 'catalog-subtitle', model.subtitle), element('p', 'catalog-description', model.description));
    const metadata = element('div', 'catalog-metadata', model.runtime);
    if (model.metadata?.revision) metadata.append(element('span', 'catalog-revision', `Revision ${model.metadata.revision.slice(0, 8)}`));
    const button = element('button', 'catalog-open', 'Open in playground'); button.type = 'button';
    button.disabled = state.busy;
    button.addEventListener('click', () => { selectModel(model.id); navigate('playground'); });
    card.append(metadata, button); return card;
  }));
}
function navigate(view) {
  state.view = view;
  for (const name of ['playground', 'models', 'runs']) {
    show(`view-${name}`, name === view);
    $(`nav-${name}`).setAttribute('aria-current', name === view ? 'page' : 'false');
  }
  text('page-title', { playground: 'Your models. One playground.', models: 'Model catalog', runs: 'Session runs' }[view]);
  text('page-subtitle', { playground: 'Experiment with real model inference', models: 'One workspace, distinct capabilities', runs: 'Your experiments, only in this tab' }[view]);
  if (view === 'models') renderCatalog(); if (view === 'runs') renderRuns();
}
function selectModel(id) {
  if (state.busy) return;
  const model = state.models.find((item) => item.id === id); if (!model) return;
  state.selected = id; state.task = model.task; $('provider-consent').checked = false; clearError(); renderModel();
}

function renderRuns() {
  text('session-count', String(state.runs.length));
  const timed = state.runs.filter((run) => Number.isFinite(run.data.processing_time_ms));
  text('session-latency', timed.length ? formatMilliseconds(timed.reduce((sum, run) => sum + run.data.processing_time_ms, 0) / timed.length) : '—');
  show('runs-empty', !state.runs.length); $('clear-runs').disabled = !state.runs.length;
  $('runs-list').replaceChildren(...state.runs.map((run) => {
    const item = element('li', 'run-item');
    const icon = element('span', 'run-icon', run.model.task === 'sentiment' ? 'Aa' : run.model.task === 'generate' ? '✦' : '♫');
    icon.setAttribute('aria-hidden', 'true');
    const details = element('div', 'run-details');
    details.append(element('strong', null, run.model.name), element('p', null, run.input));
    const meta = element('div', 'run-meta');
    meta.append(element('span', null, formatMilliseconds(run.data.processing_time_ms)), element('time', null, run.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })));
    const open = element('button', 'run-open', 'View result'); open.type = 'button';
    open.disabled = run.model.task === 'speech' || state.busy;
    if (run.model.task === 'speech') open.title = 'Audio is kept only for the current result. Download it before running another request.';
    open.addEventListener('click', () => { state.result = run; renderResult(run); navigate('playground'); });
    item.append(icon, details, meta, open); return item;
  }));
}

function releaseAudio() {
  if (state.audioUrl) {
    $('audio-result').pause(); $('audio-result').removeAttribute('src'); $('audio-result').load();
    URL.revokeObjectURL(state.audioUrl); state.audioUrl = null;
  }
}
function renderResult(run, audioBlob = null) {
  releaseAudio();
  const { model, data, roundtrip } = run;
  show('result-empty', false); show('result-content', true);
  $('result-content').dataset.prediction = data.prediction || model.task;
  text('result-status', 'Completed'); text('result-model', `${model.name} · ${model.subtitle}`);
  text('metric-latency', formatMilliseconds(data.processing_time_ms)); text('metric-roundtrip', formatMilliseconds(roundtrip));
  text('metric-tokens', model.task === 'sentiment' ? String(data.tokens_processed) : model.task === 'generate' ? `${data.input_tokens} in / ${data.output_tokens} out` : '—');
  show('result-confidence-wrap', model.task === 'sentiment'); show('result-text', model.task === 'generate' || model.task === 'transcribe');
  show('audio-result', model.task === 'speech' && Boolean(audioBlob)); show('audio-download', model.task === 'speech' && Boolean(audioBlob));
  show('truncated-notice', data.truncated === true || data.truncated_input === true);
  if (model.task === 'sentiment') {
    text('result-value', data.prediction === 'positive' ? 'Positive sentiment' : 'Negative sentiment');
    const percent = data.confidence * 100;
    text('confidence-value', `${percent.toFixed(1)}%`); $('confidence-bar').value = percent;
    text('result-description', 'Predicted-class softmax score, not calibrated certainty. This binary model has no neutral class.');
    text('result-text', '');
  } else if (model.task === 'generate') {
    text('result-value', 'Generated response'); text('result-description', `${data.provider === 'local' ? 'Local model' : 'Hosted provider'} · actual API output`);
    text('result-text', data.output);
  } else if (model.task === 'transcribe') {
    text('result-value', 'Audio transcribed');
    text('result-description', Number.isFinite(data.duration_seconds) ? `${data.duration_seconds.toFixed(1)} seconds of audio · actual provider output` : 'Actual provider output');
    text('result-text', data.text);
  } else {
    text('result-value', 'Your voice, rendered');
    text('result-description', `${formatBytes(data.audio_bytes)} WAV${data.sample_rate ? ` · ${data.sample_rate.toLocaleString()} Hz` : ''}`);
    text('result-text', '');
    if (audioBlob) {
      state.audioUrl = URL.createObjectURL(audioBlob);
      $('audio-result').src = state.audioUrl; $('audio-download').href = state.audioUrl; $('audio-download').download = 'inference-studio-speech.wav';
    }
  }
  text('result-json', JSON.stringify(data, null, 2)); $('copy-result').disabled = false; $('download-result').disabled = false;
  setResultView('overview');
}
function setResultView(view) {
  show('result-overview', view === 'overview'); show('result-json', view === 'json');
  document.querySelectorAll('#result-tabs button').forEach((button) => {
    const selected = button.dataset.resultView === view;
    button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
  });
}

async function refreshStatus() {
  if (state.refreshPromise) return state.refreshPromise;
  $('refresh-status').disabled = true;
  state.refreshPromise = (async () => {
    const [config, live, ready] = await Promise.allSettled([
      apiRequest('/studio/config'), apiRequest('/health/live'), apiRequest('/health/ready'),
    ]);
    state.configLoaded = config.status === 'fulfilled';
    if (state.configLoaded) state.config = normalizeConfig(config.value);
    state.backendAlive = live.status === 'fulfilled' && live.value?.status === 'alive';
    state.baselineReady = ready.status === 'fulfilled' && ready.value?.status === 'ready';
    let metadata = [], capabilities = {}, rejected = false;
    if (state.configLoaded && state.backendAlive && (!state.config.auth_required || state.key)) {
      const key = state.key;
      const results = await Promise.allSettled([
        apiRequest('/api/models', { key }), apiRequest('/api/capabilities', { key }),
      ]);
      rejected = results.some((result) => result.status === 'rejected' && result.reason.status === 401);
      state.authorized = !rejected && results.every((result) => result.status === 'fulfilled');
      if (state.authorized) { metadata = results[0].value; capabilities = results[1].value; }
    } else state.authorized = false;
    state.models = modelStates(metadata, capabilities, state.config, state.baselineReady);
    const availability = !state.configLoaded || !state.backendAlive ? 'unavailable'
      : !state.authorized ? 'locked' : state.baselineReady ? 'ready' : 'loading';
    $('service-status').dataset.state = availability;
    text('service-status-label', availability === 'ready' ? 'API connected' : availability === 'locked' ? 'Session access needed' : availability === 'loading' ? 'Baseline not ready' : 'API offline');
    text('auth-state', !state.configLoaded ? 'Check connection' : !state.config.auth_required ? 'Local access' : state.authorized ? 'Authenticated' : 'Add API key');
    $('auth-open').dataset.state = state.authorized ? 'ready' : 'locked';
    renderModel(); renderCatalog();
    if (rejected) showError('The server rejected the API key. Open Session access to update it.');
  })().catch(() => {
    state.authorized = false; state.configLoaded = false;
    state.models = modelStates([], {}, state.config, false);
    $('service-status').dataset.state = 'unavailable'; text('service-status-label', 'API offline'); renderModel();
  }).finally(() => { state.refreshPromise = null; $('refresh-status').disabled = false; });
  return state.refreshPromise;
}

async function runModel(event) {
  event.preventDefault(); if (state.busy) return;
  clearError();
  const model = { ...selectedModel() };
  let request;
  try {
    if (!state.configLoaded || !state.authorized) throw new StudioError('Connect to the API and unlock session access before running a model.');
    request = buildRequest(model, $('input-text').value, {
      maxTokens: $('max-output-tokens').value, voice: $('voice-select').value,
      file: $('audio-file').files[0], providerConsent: $('provider-consent').checked,
    }, state.config);
  } catch (error) { showError(error.message); return; }
  state.busy = true; state.request = new AbortController();
  if (state.result) text('result-status', 'Previous result');
  updateInput(); renderCatalog(); renderRuns();
  const started = performance.now();
  const excerpt = model.task === 'transcribe' ? $('audio-file').files[0].name : $('input-text').value;
  try {
    const result = await apiRequest(request.path, {
      key: state.key, method: 'POST', body: request.body, contentType: request.contentType,
      signal: state.request.signal, timeout: model.task === 'sentiment' ? 30000 : 180000,
    });
    validateResult(model, result);
    const run = { model, data: serializableResult(result), input: characterCount(excerpt) > 120 ? `${Array.from(excerpt).slice(0, 120).join('')}…` : excerpt,
      roundtrip: performance.now() - started, time: new Date() };
    state.result = run; state.runs.unshift(run); state.runs = state.runs.slice(0, 20);
    renderResult(run, result.blob); renderRuns();
    toast(`${model.name} completed. Real response received.`);
  } catch (error) {
    showError(error.message);
    if (error.status === 401) { state.authorized = false; await refreshStatus(); }
    if (error.status === 503) await refreshStatus();
  } finally { state.busy = false; state.request = null; updateInput(); renderCatalog(); renderRuns(); }
}

function openAuth() {
  $('api-key').value = ''; text('auth-error', ''); show('auth-error', false);
  $('auth-dialog').showModal(); $('api-key').focus();
}
function closeAuth() { $('api-key').value = ''; $('auth-dialog').close(); }
async function saveAuth(event) {
  event.preventDefault();
  const key = $('api-key').value.trim();
  if (!key || /[\r\n]/.test(key)) { text('auth-error', 'Enter a nonempty API key without line breaks.'); show('auth-error', true); return; }
  $('auth-save').disabled = true; show('auth-error', false);
  if (state.refreshPromise) await state.refreshPromise;
  state.key = key; $('api-key').value = '';
  await refreshStatus(); $('auth-save').disabled = false;
  if (state.authorized) { closeAuth(); clearError(); toast('Session connected. The key is kept only in this tab’s memory.'); }
  else { state.key = ''; text('auth-error', 'Could not authenticate. Check the key and server connection.'); show('auth-error', true); }
}

$('run-form').addEventListener('submit', runModel);
$('input-text').addEventListener('input', updateInput);
$('audio-file').addEventListener('change', () => { clearError(); updateInput(); });
$('max-output-tokens').addEventListener('input', updateInput);
$('provider-consent').addEventListener('change', updateInput);
$('clear-input').addEventListener('click', () => { $('input-text').value = ''; $('audio-file').value = ''; clearError(); updateInput(); $('input-text').focus(); });
$('cancel-run').addEventListener('click', () => { state.request?.abort(); });
$('model-select').addEventListener('change', (event) => { selectModel(event.target.value); });
$('task-tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-task]'); if (!button || state.busy) return;
  const models = state.models.filter((model) => model.task === button.dataset.task);
  const model = models.find((item) => item.state === 'ready') || models[0]; if (model) selectModel(model.id);
});
$('examples').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-example-index]'); if (!button) return;
  $('input-text').value = SAMPLES[state.task][Number(button.dataset.exampleIndex)][1]; clearError(); updateInput(); $('input-text').focus();
});
for (const view of ['playground', 'models', 'runs']) $(`nav-${view}`).addEventListener('click', () => navigate(view));
$('refresh-status').addEventListener('click', () => { clearError(); refreshStatus(); });
$('result-tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-result-view]'); if (button) setResultView(button.dataset.resultView);
});
for (const id of ['task-tabs', 'result-tabs']) $(id).addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  const tabs = [...$(id).querySelectorAll('[role="tab"]')].filter((tab) => !tab.disabled);
  const index = tabs.indexOf(event.target); if (index < 0) return;
  event.preventDefault();
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
    : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].focus(); tabs[next].click();
});
$('auth-open').addEventListener('click', openAuth);
$('auth-form').addEventListener('submit', saveAuth);
$('auth-cancel').addEventListener('click', closeAuth);
$('auth-dialog').addEventListener('close', () => { $('api-key').value = ''; });
$('auth-dialog').addEventListener('click', (event) => { if (event.target === $('auth-dialog')) closeAuth(); });
$('auth-clear').addEventListener('click', async () => { state.key = ''; closeAuth(); await refreshStatus(); clearError(); toast('Session key cleared from memory.'); });
$('copy-result').addEventListener('click', async () => {
  if (!state.result) return;
  try { await navigator.clipboard.writeText(JSON.stringify(state.result.data, null, 2)); toast('Response JSON copied.'); }
  catch { toast('Clipboard permission unavailable. Use Download JSON instead.'); }
});
$('download-result').addEventListener('click', () => {
  if (!state.result) return;
  const blob = new Blob([JSON.stringify(state.result.data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob); const anchor = document.createElement('a');
  anchor.href = url; anchor.download = 'inference-studio-result.json'; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000); toast('Response JSON downloaded.');
});
$('clear-runs').addEventListener('click', () => {
  state.runs = []; state.result = null; releaseAudio(); renderRuns();
  show('result-content', false); show('result-empty', true); text('result-json', '');
  $('copy-result').disabled = true; $('download-result').disabled = true;
  toast('Session history and current result cleared.');
});
document.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && state.view === 'playground' && !$('auth-dialog').open) {
    event.preventDefault(); if (!$('run-button').disabled) $('run-form').requestSubmit();
  }
});
window.addEventListener('pagehide', () => {
  state.key = ''; state.authorized = false; state.runs = []; state.result = null;
  state.request?.abort(); releaseAudio(); closeAuth(); renderRuns();
  $('input-text').value = ''; $('audio-file').value = ''; $('provider-consent').checked = false;
  text('result-json', ''); text('result-text', ''); text('result-value', '');
  show('result-content', false); show('result-empty', true); show('toast', false);
});
window.addEventListener('pageshow', (event) => { if (event.persisted) { renderRuns(); show('result-content', false); show('result-empty', true); refreshStatus(); } });
setInterval(() => { if (!document.hidden && !state.busy && !$('auth-dialog').open) refreshStatus(); }, 15000);
navigate('playground'); renderModel(); renderRuns(); refreshStatus();
