// Bounded k6 benchmark for the CPU sentiment API (docs/KUBERNETES.md).
//
// Scenario (sequential, in one run):
//   warmup    15s @ 2 VUs   (fills caches, verifies the service)
//   baseline  30s @ 2 VUs   (one-replica capacity: matches 2 inference slots)
//   sustained LOADTEST_DURATION (default 180s) @ LOADTEST_VUS (default 8)
//
// Hard bounds: VUs capped at 32, sustained duration capped at 600s (a
// zero/unparseable LOADTEST_DURATION falls back to the default), optional
// think time capped at 1000ms (default 0 so sustained load can actually
// saturate CPU and drive the HPA), and gracefulStop '0s' on every scenario
// so warmup/baseline/sustained can never overlap (requests still in flight
// at a phase boundary are interrupted; they are counted as attempts and
// appear as the interrupted remainder, never as successes).
//
// Accounting: every attempt is counted BEFORE the request is sent, so
// completed, invalid, and interrupted attempts are distinguishable.
//
// Connection handling: noConnectionReuse is deliberately TRUE. With keep-alive,
// a VU's connections stick to the Pod that accepted them, so scale-out replicas
// can receive zero load even though traffic targets the Service. A fresh
// connection per request lets the Service re-balance across ready Pods
// (tradeoff: extra TCP handshake latency/CPU at the generator and node).
//
// Metrics separation: latency trends and per-phase counters for SUCCESSFUL
// (200 AND body-validated) predictions are kept apart from invalid 200
// bodies, 429s, 4xx/5xx and other statuses. A malformed 200 response is never
// counted as successful traffic. Aggregate attempt metrics (http_reqs,
// per-phase attempt counters) are retained; the attempt rate is reported as
// ATTEMPT throughput, clearly distinguished from the separate successful
// (200) throughput. Warmup data never pollutes baseline/sustained results
// because every metric is per-phase.
//
// The API_KEY environment variable, when set, is sent as a Bearer token and
// is never logged, echoed or included in the summary.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const TARGET = (__ENV.TARGET_URL || 'http://ai-model-serving.ai-serving.svc.cluster.local:8000').replace(/\/+$/, '');
const MODEL = __ENV.LOADTEST_MODEL || 'sentiment';
const API_KEY = __ENV.API_KEY || '';

function envInt(name, fallback, max) {
  const parsed = parseInt(__ENV[name] || String(fallback), 10);
  if (isNaN(parsed) || parsed < 1) return fallback;
  return Math.min(parsed, max);
}
function envDurationSeconds(name, fallbackSeconds, maxSeconds) {
  const raw = __ENV[name] || `${fallbackSeconds}s`;
  const match = raw.match(/^(\d+)(s|m)?$/);
  let seconds = fallbackSeconds;
  if (match) {
    seconds = parseInt(match[1], 10) * (match[2] === 'm' ? 60 : 1);
  } else {
    console.warn(`loadtest: ignoring unparseable ${name}=${raw}, using ${fallbackSeconds}s`);
  }
  if (seconds < 1) {
    console.warn(`loadtest: ${name}=${raw} is below 1s; using the ${fallbackSeconds}s default`);
    seconds = fallbackSeconds;
  }
  if (seconds > maxSeconds) {
    console.warn(`loadtest: ${name}=${seconds}s exceeds the ${maxSeconds}s cap; capping`);
    seconds = maxSeconds;
  }
  return seconds;
}

const VUS = envInt('LOADTEST_VUS', 8, 32);
const SUSTAINED_SECONDS = envDurationSeconds('LOADTEST_DURATION', 180, 600);
const THINK_SECONDS = envInt('LOADTEST_THINK_TIME_MS', 0, 1000) / 1000;
const WARMUP_SECONDS = 15;
const BASELINE_SECONDS = 30;
const WARMUP_VUS = 2;
const BASELINE_VUS = 2;

http.setResponseCallback(http.expectedStatuses(200, 429));

const SENTENCES = [
  'I love this product. It is absolutely wonderful!',
  'What a fantastic experience from start to finish.',
  'The service was excellent and the staff were kind.',
  'Best purchase I have made in years, highly recommended.',
  'Absolutely delightful, exceeded every expectation.',
  'This is the greatest thing since sliced bread.',
  'A brilliant performance that kept me engaged throughout.',
  'I am extremely happy with the results.',
  'I hate this product. It is terrible and disappointing.',
  'The worst experience I have had in a long time.',
  'Awful service, rude staff, and a broken product.',
  'Complete waste of money, I regret this purchase.',
  'The quality is poor and it broke within a week.',
  'A dreadful film, boring and badly acted.',
  'Deeply unsatisfying and frustrating to use.',
  'I would not recommend this to anyone, ever.',
  'Terrible support, they never answered my messages.',
  'This update ruined everything I liked about it.',
];

const PHASES = ['warmup', 'baseline', 'sustained'];
// Per-phase attempts and outcome counters: warmup never pollutes measured
// phases, and completed/invalid/interrupted stay distinguishable.
const COUNTERS = {};
for (const phase of PHASES) {
  COUNTERS[`attempts_${phase}`] = new Counter(`attempts_${phase}`);
  COUNTERS[`success_200_${phase}`] = new Counter(`success_200_${phase}`);
  COUNTERS[`invalid_200_${phase}`] = new Counter(`invalid_200_${phase}`);
  COUNTERS[`rate_limited_${phase}`] = new Counter(`rate_limited_${phase}`);
  COUNTERS[`errors_4xx_${phase}`] = new Counter(`errors_4xx_${phase}`);
  COUNTERS[`errors_5xx_${phase}`] = new Counter(`errors_5xx_${phase}`);
  COUNTERS[`other_${phase}`] = new Counter(`other_${phase}`);
}
// Aggregate outcome counters by status (all phases combined). requests_200
// counts ONLY body-validated successes; malformed 200 bodies are separate.
const requests200 = new Counter('requests_200');
const requests200Invalid = new Counter('requests_200_invalid');
const requests429 = new Counter('requests_429');
const requests4xx = new Counter('requests_4xx');
const requests5xx = new Counter('requests_5xx');
const requestsOther = new Counter('requests_other');
// Latency of body-validated (200) predictions only, per measured phase.
const LATENCY_TRENDS = {};
for (const phase of PHASES) {
  LATENCY_TRENDS[phase] = new Trend(`latency_success_${phase}_ms`, true);
}
// Rejections, failures, and invalid bodies are timed separately.
const latencyRateLimited = new Trend('latency_rate_limited_ms', true);
const latencyErrors = new Trend('latency_errors_ms', true);
const latencyInvalid = new Trend('latency_invalid_ms', true);

// A response only counts as a successful prediction when the body has the
// real /api/inference response shape.
function isValidSentimentResponse(response) {
  try {
    const body = response.json();
    return (
      body !== null && typeof body === 'object' &&
      body.status === 'success' &&
      (body.prediction === 'positive' || body.prediction === 'negative') &&
      typeof body.confidence === 'number' &&
      body.confidence >= 0 && body.confidence <= 1 &&
      Number.isFinite(body.tokens_processed) && body.tokens_processed > 0 &&
      typeof body.processing_time_ms === 'number' &&
      body.processing_time_ms >= 0
    );
  } catch {
    return false;
  }
}

function recordOutcome(response, phase, started) {
  const latency = Date.now() - started;
  const status = response.status;
  if (status === 200) {
    if (isValidSentimentResponse(response)) {
      requests200.add(1);
      COUNTERS[`success_200_${phase}`].add(1);
      LATENCY_TRENDS[phase].add(latency);
    } else {
      // A 200 with a malformed body is an error, never a success.
      requests200Invalid.add(1);
      COUNTERS[`invalid_200_${phase}`].add(1);
      latencyInvalid.add(latency);
    }
  } else if (status === 429) {
    requests429.add(1);
    COUNTERS[`rate_limited_${phase}`].add(1);
    latencyRateLimited.add(latency);
  } else if (status >= 500) {
    requests5xx.add(1);
    COUNTERS[`errors_5xx_${phase}`].add(1);
    latencyErrors.add(latency);
  } else if (status >= 400) {
    requests4xx.add(1);
    COUNTERS[`errors_4xx_${phase}`].add(1);
    latencyErrors.add(latency);
  } else {
    // Redirects and any other unexpected status.
    requestsOther.add(1);
    COUNTERS[`other_${phase}`].add(1);
  }
}

function doRequest(phase) {
  // Counted BEFORE the request: an attempt that is interrupted (gracefulStop
  // boundary, deadline, or connection error) stays visible as
  // attempts minus completions, never as a success.
  COUNTERS[`attempts_${phase}`].add(1);
  const sentence = SENTENCES[Math.floor(Math.random() * SENTENCES.length)];
  const payload = JSON.stringify({ model: MODEL, input: sentence });
  const headers = { 'Content-Type': 'application/json' };
  if (API_KEY) {
    headers['Authorization'] = `Bearer ${API_KEY}`;
  }
  const started = Date.now();
  const response = http.post(`${TARGET}/api/inference`, payload, { headers });
  recordOutcome(response, phase, started);
  check(response, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
  });
  if (response.status === 200) {
    check(response, { 'valid sentiment response': (r) => isValidSentimentResponse(r) });
  }
  if (response.status >= 500) {
    console.error(`loadtest: server error status ${response.status}`);
  }
  if (THINK_SECONDS > 0) {
    sleep(THINK_SECONDS);
  }
}

export const options = {
  scenarios: {
    warmup: {
      executor: 'constant-vus',
      exec: 'warmupRequest',
      startTime: '0s',
      duration: `${WARMUP_SECONDS}s`,
      vus: WARMUP_VUS,
      // gracefulStop 0s: in-flight requests at the boundary are interrupted
      // and show up as the interrupted remainder, keeping phases disjoint.
      gracefulStop: '0s',
    },
    baseline: {
      executor: 'constant-vus',
      exec: 'baselineRequest',
      startTime: `${WARMUP_SECONDS}s`,
      duration: `${BASELINE_SECONDS}s`,
      vus: BASELINE_VUS,
      gracefulStop: '0s',
    },
    sustained: {
      executor: 'constant-vus',
      exec: 'sustainedRequest',
      startTime: `${WARMUP_SECONDS + BASELINE_SECONDS}s`,
      duration: `${SUSTAINED_SECONDS}s`,
      vus: VUS,
      gracefulStop: '0s',
    },
  },
  thresholds: {
    // Soft gates: mark the run failed in the summary but never abort mid-load.
    'http_req_failed': ['rate<0.05'],
    'checks': ['rate>0.95'],
  },
  // Keep-alive connections would pin each VU to the first-accepting Pod, so
  // scale-out replicas could see zero load even though traffic targets the
  // Service. Fresh connections let the Service re-balance every request
  // (cost: one TCP handshake per request at the generator and node).
  noConnectionReuse: true,
  userAgent: 'ccd-lab-k6/1.0',
};

export function warmupRequest() {
  doRequest('warmup');
}
export function baselineRequest() {
  doRequest('baseline');
}
export function sustainedRequest() {
  doRequest('sustained');
}
export default function () {
  doRequest('sustained');
}

function metricValue(metrics, name, key, fallback) {
  const metric = metrics[name];
  if (!metric || !metric.values || metric.values[key] === undefined) return fallback;
  return metric.values[key];
}

function counterValue(metrics, name) {
  return metricValue(metrics, name, 'count', 0);
}

function phaseSummary(metrics, phase, phaseSeconds) {
  const attempts = counterValue(metrics, `attempts_${phase}`);
  const success = counterValue(metrics, `success_200_${phase}`);
  const invalid = counterValue(metrics, `invalid_200_${phase}`);
  const rateLimited = counterValue(metrics, `rate_limited_${phase}`);
  const errors4xx = counterValue(metrics, `errors_4xx_${phase}`);
  const errors5xx = counterValue(metrics, `errors_5xx_${phase}`);
  const other = counterValue(metrics, `other_${phase}`);
  const completed = success + invalid + rateLimited + errors4xx + errors5xx + other;
  return {
    phase_duration_s: phaseSeconds,
    attempts: attempts,
    completed: completed,
    interrupted: attempts - completed,
    success_200: success,
    invalid_200: invalid,
    rate_limited: rateLimited,
    errors_4xx: errors4xx,
    errors_5xx: errors5xx,
    other: other,
    attempt_throughput_rps: phaseSeconds > 0 ? attempts / phaseSeconds : 0,
    successful_throughput_rps: phaseSeconds > 0 ? success / phaseSeconds : 0,
    latency_success_p95_ms: metricValue(metrics, `latency_success_${phase}_ms`, 'p(95)', null),
    latency_success_mean_ms: metricValue(metrics, `latency_success_${phase}_ms`, 'avg', null),
  };
}

export function handleSummary(data) {
  const metrics = data.metrics || {};
  const durationMs = (data.state && data.state.testRunDurationMs) || 0;
  const durationS = durationMs > 0 ? durationMs / 1000 : 0;
  const successful = counterValue(metrics, 'requests_200');
  const summary = {
    target: TARGET,
    scenario: {
      warmup_seconds: WARMUP_SECONDS,
      warmup_vus: WARMUP_VUS,
      baseline_seconds: BASELINE_SECONDS,
      baseline_vus: BASELINE_VUS,
      sustained_seconds: SUSTAINED_SECONDS,
      sustained_vus: VUS,
      think_time_ms: THINK_SECONDS * 1000,
      no_connection_reuse: true,
      graceful_stop_seconds: 0,
    },
    totals: {
      duration_ms: durationMs,
      attempts_total: metricValue(metrics, 'http_reqs', 'count', 0),
      // Attempt throughput counts every request incl. rejections; it must
      // not be presented as successful traffic. Successful (200,
      // body-validated) throughput is reported separately below.
      attempt_throughput_rps: metricValue(metrics, 'http_reqs', 'rate', 0),
      successful_200: successful,
      successful_throughput_rps: durationS > 0 ? successful / durationS : 0,
      requests_200_invalid: counterValue(metrics, 'requests_200_invalid'),
      requests_429: counterValue(metrics, 'requests_429'),
      requests_4xx: counterValue(metrics, 'requests_4xx'),
      requests_5xx: counterValue(metrics, 'requests_5xx'),
      requests_other: counterValue(metrics, 'requests_other'),
    },
    phases: {
      warmup: phaseSummary(metrics, 'warmup', WARMUP_SECONDS),
      baseline: phaseSummary(metrics, 'baseline', BASELINE_SECONDS),
      sustained: phaseSummary(metrics, 'sustained', SUSTAINED_SECONDS),
    },
    latency_ms: {
      rate_limited_p95: metricValue(metrics, 'latency_rate_limited_ms', 'p(95)', null),
      errors_p95: metricValue(metrics, 'latency_errors_ms', 'p(95)', null),
      invalid_200_p95: metricValue(metrics, 'latency_invalid_ms', 'p(95)', null),
      attempts_overall_p95: metricValue(metrics, 'http_req_duration', 'p(95)', null),
    },
    checks_rate: metricValue(metrics, 'checks', 'rate', null),
    http_req_failed_rate: metricValue(metrics, 'http_req_failed', 'rate', null),
  };
  console.log('loadtest: summary follows (no credentials are printed)');
  return {
    stdout: JSON.stringify(summary, null, 2) + '\n',
    'summary.json': JSON.stringify(summary, null, 2) + '\n',
  };
}
