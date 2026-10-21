import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MODELS, DEFAULT_CONFIG, StudioError, normalizeConfig, modelStates, characterCount,
  jsonBytes, formatMilliseconds, audioContentType, buildRequest, apiRequest,
  statusMessage, validateResult, serializableResult,
} from '../../app/static/studio-core.mjs';

const models = modelStates([{ id: 'sentiment', ready: true, max_input_chars: 2000 }], {
  capabilities: Object.fromEntries(MODELS.filter(m => m.capability).map(m => [m.capability, { state: 'ready', voices: ['Bella'] }])),
}, DEFAULT_CONFIG, true);
const model = id => models.find(m => m.id === id);
const positive = { status: 'success', model: 'sentiment', prediction: 'positive', confidence: 0.95, processing_time_ms: 5, tokens_processed: 8, truncated: false };
const generation = { status: 'success', model: 'qwen3.5-0.8b', provider: 'local', output: 'real output', input_tokens: 5, output_tokens: 7, processing_time_ms: 9, truncated_input: false };
const transcription = { status: 'success', model: 'whisper-large-v3-turbo', text: 'actual words', processing_time_ms: 12, duration_seconds: 0.5 };
const fakeFile = (name = 'clip.wav', type = 'audio/wav', size = 10) => ({ name, type, size });
const jsonFetch = value => async () => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const abortFetch = async (_path, { signal }) => new Promise((_resolve, reject) => {
  if (signal.aborted) reject(new DOMException('Aborted', 'AbortError'));
  else signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
});

test('Unicode characters follow Python code-point lengths, not UTF-16 units', () => {
  assert.equal(characterCount('A😀ह'), 3); assert.equal(characterCount('e\u0301'), 2);
});
test('JSON limits use actual UTF-8 bytes including serialized overhead', () => {
  assert.equal(jsonBytes({ input: '😀' }), Buffer.byteLength(JSON.stringify({ input: '😀' })));
  assert.ok(jsonBytes({ input: 'ह' }) > JSON.stringify({ input: 'ह' }).length);
});
test('config exposes only safe flags and positive integer limits', () => {
  assert.deepEqual(normalizeConfig(), DEFAULT_CONFIG);
  const config = normalizeConfig({ api_key: 'must-not-copy', auth_required: true, max_request_bytes: 2, speech_max_input_chars: -1, generation_max_output_tokens: 1.5 });
  assert.equal(config.auth_required, true); assert.equal(config.max_request_bytes, 2);
  assert.equal(config.speech_max_input_chars, 1000); assert.equal(config.generation_max_output_tokens, 256);
  assert.equal('api_key' in config, false);
});
test('missing or malformed model metadata fails closed', () => {
  for (const metadata of [undefined, {}, [], [null]]) assert.equal(modelStates(metadata, null)[0].state, 'unavailable');
  assert.ok(modelStates([], {}).every(m => m.state === 'unavailable'));
});
test('sentiment requires both metadata readiness and real health readiness', () => {
  assert.equal(modelStates([{ id: 'sentiment', ready: true }], {}, DEFAULT_CONFIG, false)[0].state, 'unavailable');
  assert.equal(modelStates([{ id: 'sentiment', ready: false }], {}, DEFAULT_CONFIG, true)[0].state, 'unavailable');
  assert.equal(models[0].state, 'ready');
});
test('capabilities accept exact states, string voices, and bounded metadata limits', () => {
  const values = modelStates([{ id: 'sentiment', ready: true, max_input_chars: 9000 }], { capabilities: {
    'qwen-generation': { state: 'fabricated' }, 'kitten-speech': { state: 'disabled', voices: ['Bella', 123] },
  } }, DEFAULT_CONFIG, true);
  assert.equal(values[0].maxChars, 2000); assert.equal(values[1].state, 'unavailable');
  assert.equal(values[3].state, 'disabled'); assert.deepEqual(values[3].voices, ['Bella']);
});
test('formatting never invents missing or invalid timings', () => {
  for (const value of [null, undefined, NaN, -1, '12']) assert.equal(formatMilliseconds(value), '—');
  assert.equal(formatMilliseconds(0), '0.00 ms'); assert.equal(formatMilliseconds(12), '12.0 ms');
});

test('sentiment request matches the strict API schema', () => {
  const request = buildRequest(model('sentiment'), 'great');
  assert.equal(request.path, '/api/inference'); assert.equal(request.contentType, 'application/json');
  assert.deepEqual(JSON.parse(request.body), { model: 'sentiment', input: 'great' });
});
test('generation request matches local and hosted model contracts', () => {
  for (const id of ['qwen3.5-0.8b', 'groq-gpt-oss-120b']) {
    const request = buildRequest(model(id), 'explain this', { maxTokens: '128', providerConsent: true });
    assert.equal(request.path, '/api/generate');
    assert.deepEqual(JSON.parse(request.body), { model: id, input: 'explain this', max_output_tokens: 128 });
  }
});
test('speech request omits model and absent voice, accepts advertised voice', () => {
  assert.deepEqual(JSON.parse(buildRequest(model('kitten-speech'), 'hello').body), { input: 'hello' });
  assert.deepEqual(JSON.parse(buildRequest(model('kitten-speech'), 'hello', { voice: 'Bella' }).body), { input: 'hello', voice: 'Bella' });
  assert.throws(() => buildRequest(model('kitten-speech'), 'hello', { voice: 'unknown' }), StudioError);
});
test('transcription uses exact raw audio body rather than multipart or URLs', () => {
  const file = fakeFile(); const request = buildRequest(model('groq-transcription'), '', { file, providerConsent: true });
  assert.equal(request.path, '/api/transcriptions'); assert.equal(request.body, file); assert.equal(request.contentType, 'audio/wav');
});
for (const id of ['groq-gpt-oss-120b', 'groq-transcription']) test(`${id} requires explicit hosted-provider consent`, () => {
  assert.throws(() => buildRequest(model(id), 'hello', { maxTokens: 128, file: fakeFile() }), /Confirm hosted-provider/);
});
for (const readiness of ['disabled', 'loading', 'unavailable']) test(`${readiness} adapters cannot send requests`, () => {
  assert.throws(() => buildRequest({ ...model('sentiment'), state: readiness }, 'hello'), /not ready/);
});
for (const input of ['', ' ', '\n\t', undefined, null]) test(`blank/invalid text (${JSON.stringify(input)}) is refused`, () => {
  assert.throws(() => buildRequest(model('sentiment'), input), StudioError);
});
test('text character limits support supplementary-plane Unicode', () => {
  assert.doesNotThrow(() => buildRequest(model('sentiment'), '😀'.repeat(2000)));
  assert.throws(() => buildRequest(model('sentiment'), '😀'.repeat(2001)), /characters/);
});
for (const maxTokens of [0, -1, 257, 1.5, undefined, 'not-a-number']) test(`invalid output-token limit (${maxTokens}) is refused`, () => {
  assert.throws(() => buildRequest(model('qwen3.5-0.8b'), 'hello', { maxTokens }), /Output tokens/);
});
test('JSON byte limit covers multibyte content and schema overhead', () => {
  const input = '😀'.repeat(10); const exact = jsonBytes({ model: 'sentiment', input });
  assert.doesNotThrow(() => buildRequest(model('sentiment'), input, {}, { ...DEFAULT_CONFIG, max_request_bytes: exact }));
  assert.throws(() => buildRequest(model('sentiment'), input, {}, { ...DEFAULT_CONFIG, max_request_bytes: exact - 1 }), /request-byte/);
});
test('all backend-supported audio MIME aliases are preserved', () => {
  for (const type of ['audio/wav', 'audio/x-wav', 'audio/wave', 'audio/vnd.wave', 'audio/mpeg', 'audio/mp3', 'audio/mpga', 'audio/flac', 'audio/x-flac', 'audio/ogg', 'application/ogg', 'audio/webm', 'audio/mp4', 'audio/m4a', 'audio/x-m4a']) {
    assert.equal(audioContentType(fakeFile('clip', type)), type);
  }
  assert.equal(audioContentType(fakeFile('clip.WAV', '')), 'audio/wav');
  assert.equal(audioContentType(fakeFile('clip.mp3', 'application/octet-stream')), 'audio/mpeg');
  assert.equal(audioContentType(fakeFile('bad.exe', 'application/octet-stream')), null);
});
test('raw audio respects BOTH global and transcription size caps', () => {
  const m = model('groq-transcription'); const options = { providerConsent: true };
  assert.throws(() => buildRequest(m, '', { ...options, file: fakeFile('clip.wav', 'audio/wav', 0) }), /nonempty/);
  assert.throws(() => buildRequest(m, '', { ...options, file: fakeFile('clip.wav', 'audio/wav', 65537) }), /request limit/);
  assert.throws(() => buildRequest(m, '', { ...options, file: fakeFile('clip.wav', 'audio/wav', 11) }, { ...DEFAULT_CONFIG, transcription_max_bytes: 10 }), /request limit/);
  assert.doesNotThrow(() => buildRequest(m, '', { ...options, file: fakeFile('clip.wav', 'audio/wav', 65536) }));
});

test('fetches are relative, same-origin, no-store and reject redirects', async () => {
  let captured;
  await apiRequest('/api/inference', { key: 'memory-key', method: 'POST', body: '{}', contentType: 'application/json', fetcher: async (path, options) => {
    captured = { path, options }; return new Response('{}');
  } });
  assert.equal(captured.path, '/api/inference'); assert.equal(captured.options.headers.Authorization, 'Bearer memory-key');
  assert.equal(captured.options.credentials, 'same-origin'); assert.equal(captured.options.cache, 'no-store');
  assert.equal(captured.options.redirect, 'error'); assert.equal(captured.options.headers['Content-Type'], 'application/json');
});
for (const path of ['/studio/config', '/health/live', '/health/ready']) test(`${path} never receives the API key`, async () => {
  await apiRequest(path, { key: 'memory-key', fetcher: async (_url, options) => {
    assert.equal('Authorization' in options.headers, false); return new Response('{}');
  } });
});
for (const path of ['https://example.com/api/inference', '//evil.example/api/models', '/api/unknown', '/api/models?key=secret']) test(`unsafe/nonallowlisted URL (${path}) is refused before fetch`, async () => {
  let calls = 0;
  await assert.rejects(apiRequest(path, { fetcher: async () => { calls++; } }), /allowlisted/);
  assert.equal(calls, 0);
});
test('unsupported fetch methods are refused', async () => {
  await assert.rejects(apiRequest('/api/models', { method: 'DELETE' }), /allowlisted/);
});
for (const status of [401, 403, 404, 413, 415, 422, 429, 500, 502, 503, 504]) test(`HTTP ${status} is sanitized and never automatically retried`, async () => {
  let calls = 0;
  await assert.rejects(apiRequest('/api/inference', { method: 'POST', fetcher: async () => {
    calls++; return new Response(JSON.stringify({ detail: 'sensitive exception/key/input' }), { status });
  } }), error => error instanceof StudioError && error.status === status && error.message === statusMessage(status) && !error.message.includes('sensitive'));
  assert.equal(calls, 1);
});
test('invalid JSON and network failures do not expose exception details', async () => {
  await assert.rejects(apiRequest('/api/models', { fetcher: async () => new Response('secret invalid JSON') }), /Cannot reach/);
  await assert.rejects(apiRequest('/api/models', { fetcher: async () => { throw new Error('private transport exception'); } }), /Cannot reach/);
});
test('browser timeout sends one request and warns that server work may continue', async () => {
  await assert.rejects(apiRequest('/api/inference', { timeout: 10, fetcher: abortFetch }), /Server work may continue; no automatic retry/);
});
test('explicit cancellation preserves distinct status and honest server semantics', async () => {
  const controller = new AbortController();
  const promise = apiRequest('/api/inference', { signal: controller.signal, fetcher: abortFetch });
  controller.abort();
  await assert.rejects(promise, error => error.status === -1 && error.message.includes('Server work may continue'));
});
test('already-aborted caller signal cancels immediately', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(apiRequest('/api/inference', { signal: controller.signal, fetcher: abortFetch }), error => error.status === -1);
});
test('WAV response preserves real audio and timing headers', async () => {
  const result = await apiRequest('/api/speech', { fetcher: async () => new Response('RIFFfixture', { headers: { 'Content-Type': 'audio/wav', 'X-Sample-Rate': '24000', 'X-Processing-Time-Ms': '9.5' } }) });
  assert.ok(result.blob instanceof Blob); assert.equal(result.processing_time_ms, 9.5); assert.equal(result.sample_rate, 24000);
  assert.equal(result.audio_bytes, 11); assert.equal(serializableResult(result).format, 'audio/wav');
  assert.equal('blob' in serializableResult(result), false);
});
test('missing audio telemetry stays null instead of fabricated zero', async () => {
  const result = await apiRequest('/api/speech', { fetcher: async () => new Response('RIFF', { headers: { 'Content-Type': 'audio/wav' } }) });
  assert.equal(result.processing_time_ms, null); assert.equal(result.sample_rate, null);
});
test('wrong-type or empty audio response is refused', async () => {
  await assert.rejects(apiRequest('/api/speech', { fetcher: jsonFetch({}) }), /expected WAV/);
  await assert.rejects(apiRequest('/api/speech', { fetcher: async () => new Response('', { headers: { 'Content-Type': 'audio/wav' } }) }), /empty audio/);
});

test('valid sentiment, generation and transcription contracts are accepted', () => {
  assert.equal(validateResult(model('sentiment'), positive), positive);
  assert.equal(validateResult(model('qwen3.5-0.8b'), generation), generation);
  assert.equal(validateResult(model('groq-transcription'), transcription), transcription);
});
for (const patch of [{ status: 'error' }, { model: 'wrong' }, { prediction: 'neutral' }, { confidence: -1 }, { confidence: 1.1 }, { confidence: NaN }, { processing_time_ms: null }, { tokens_processed: 0 }, { tokens_processed: 1.5 }, { truncated: 'false' }]) test(`invalid sentiment result ${JSON.stringify(patch)} is refused`, () => {
  assert.throws(() => validateResult(model('sentiment'), { ...positive, ...patch }), StudioError);
});
for (const patch of [{ model: 'wrong' }, { provider: 'groq' }, { output: {} }, { input_tokens: -1 }, { output_tokens: null }, { truncated_input: 'no' }, { processing_time_ms: -1 }]) test(`invalid generation result ${JSON.stringify(patch)} is refused`, () => {
  assert.throws(() => validateResult(model('qwen3.5-0.8b'), { ...generation, ...patch }), StudioError);
});
for (const patch of [{ model: 'wrong' }, { text: {} }, { duration_seconds: -1 }, { duration_seconds: '10' }]) test(`invalid transcription result ${JSON.stringify(patch)} is refused`, () => {
  assert.throws(() => validateResult(model('groq-transcription'), { ...transcription, ...patch }), StudioError);
});
test('empty/non-Blob speech results are never treated as success', () => {
  for (const blob of [undefined, 'pretend audio', new Blob([])]) assert.throws(() => validateResult(model('kitten-speech'), { blob }), StudioError);
});
