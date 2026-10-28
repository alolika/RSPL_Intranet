# Face Registration + Face Attendance — Handoff for Sister Project

Audience: a Claude Code session working in a **different repository** that needs to add a
"mark attendance by face + capture GPS location + resolve city" module. This document
distils what is actually running in production in the BNI Meeting app
(`chaptermeeting.retailware.xyz`) so the receiving team can lift the techniques
without needing access to that codebase.

Everything described here is **client-side face recognition** — the server never sees
a camera frame, only a 128-float descriptor + a reference JPEG. That property is
what makes the approach cheap (no GPU server), privacy-respectful (no biometrics on
the wire during attendance), and easy to port across stacks.

---

## 1. What the module does

Two user-facing flows, one admin and one employee:

### 1a. Face Registration (admin / HR)
1. Admin picks an employee from the directory.
2. Phone/laptop camera opens (portrait, selfie-facing).
3. Guided capture: oval guide + stability check, then **15 shots in 3 pose phases**
   (straight / slight-left / slight-right, 5 each) to build a template set that
   tolerates small head-angle variation.
4. The browser computes a 128-float face descriptor per shot and keeps the single
   best-scored frame as a reference JPEG crop.
5. Browser POSTs `{ embeddings: number[15][128], photo: jpeg }` to the server.
6. Server stores `embeddings` JSON on the employee row and the photo as a file.

### 1b. Face Attendance (employee self-service)
1. Employee opens the attendance page from any device.
2. Browser fetches **all enrolled embeddings** for the tenant/team (ETag-cached).
3. Camera opens; oval guide + framing check.
4. Optional **blink liveness** (eye-aspect-ratio drop + recovery) before matching.
   Toggleable per tenant — on for shared devices, off for personal phones.
5. Browser computes the current frame's descriptor, cosine-matches against every
   enrolled template, picks the highest similarity ≥ 0.45.
6. **At the moment of a successful match**, before POSTing, the browser *also* reads:
   - `navigator.geolocation.getCurrentPosition()` → `{ lat, lng, accuracyM }`
   - a reverse-geocode call → `{ city, state, country }`
7. Browser POSTs `{ employeeId, confidence, lat, lng, accuracyM, city, state, country }`.
8. Server stamps the attendance row with **server clock** + whatever the client sent
   for geo. Server optionally re-reverse-geocodes to validate.
9. UI shows a big splash: employee photo + "Good morning, <FirstName>" + marked-at
   time + city. Voice greeting via Web Speech API.

**Fallback**: if the face is unrecognised, if liveness fails, or if geolocation is
denied, the UI drops to manual sign-in (or blocks attendance — the sister project
should decide which; see §7).

---

## 2. The library and model files

**Package**: [`@vladmandic/face-api`](https://www.npmjs.com/package/@vladmandic/face-api).
A maintained fork of the original `face-api.js`. TF.js under the hood. One npm
install, no backend ML service, no third-party CDN at runtime.

```
npm install @vladmandic/face-api
```

**Three model bundles are needed**, totalling ~6 MB. Copy them from the installed
package into your static-asset folder so they're served from your own origin
(do **not** load from a public CDN — the models rarely change and self-hosting
avoids a cold-cache stall + external dependency):

| From `node_modules/@vladmandic/face-api/model/` | To (your app's static dir) |
|---|---|
| `tiny_face_detector_model-weights_manifest.json` | `assets/face-models/` |
| `tiny_face_detector_model.bin` | `assets/face-models/` |
| `face_landmark_68_tiny_model-weights_manifest.json` | `assets/face-models/` |
| `face_landmark_68_tiny_model.bin` | `assets/face-models/` |
| `face_recognition_model-weights_manifest.json` | `assets/face-models/` |
| `face_recognition_model.bin` | `assets/face-models/` |

Load them once per page lifecycle; subsequent calls are cheap:

```ts
import * as faceapi from '@vladmandic/face-api';

const MODEL_URL = '/assets/face-models'; // served by your static server

await Promise.all([
  faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL),
  faceapi.nets.faceLandmark68TinyNet.loadFromUri(MODEL_URL),
  faceapi.nets.faceRecognitionNet.loadFromUri(MODEL_URL),
]);
```

**Why these three and not the "non-tiny" variants?**
- `tinyFaceDetector` — fast enough for a 150-ms detection loop on a mid-range phone.
- `faceLandmark68TinyNet` — 68 landmark points, enough for the eye-aspect-ratio
  blink detector; the full model is 10× the size with no accuracy win here.
- `faceRecognitionNet` — produces the 128-float descriptor we store and compare.

---

## 3. Core constants (ship these as a single config module)

Keep them in one file so retuning is a one-line change — don't let the number
`0.45` sprinkle across routes, services, and tests.

```ts
// shared/face/config.ts
export const FACE_DESCRIPTOR_LENGTH = 128;         // model-fixed, do not change
export const FACE_MATCH_THRESHOLD   = 0.45;        // cosine; see §4 for tuning
export const FACE_ENROL_SHOT_COUNT  = 15;          // 3 poses × 5 shots
export const CAPTURES_PER_POSE      = 5;
export const POSE_PROMPTS = [
  'Look straight at the camera',
  'Turn your head slightly left',
  'Turn your head slightly right',
] as const;
export const MIN_CAPTURE_SCORE      = 0.75;        // detector score floor for enrol
export const DETECTION_INTERVAL_MS  = 150;
export const STABLE_FRAMES_REQUIRED = 10;          // enrol: hold-steady gate
export const STABLE_FRAMES_TO_MARK  = 5;           // attendance: no-blink mode
export const REFERENCE_IMAGE_SIDE   = 320;         // crop size (px, square)
export const BLINK_EAR_CLOSED       = 0.22;
export const BLINK_EAR_OPEN         = 0.23;
export const BLINK_GRACE_FRAMES     = 8;
export const BLINK_PROMPT_TIMEOUT_MS = 4500;
export const SUPPRESSION_WINDOW_MS  = 2500;        // "already marked" cool-off
export const EMBEDDINGS_REFRESH_MS  = 120_000;     // re-pull enrolled set
```

A matching Python config lives on the server so both sides agree on
`FACE_DESCRIPTOR_LENGTH = 128` and the match threshold:

```python
# server-side (any Python framework)
FACE_DESCRIPTOR_LENGTH: int = 128
FACE_MATCH_THRESHOLD: float = 0.45
```

---

## 4. The matching math

`faceapi.detectSingleFace(video).withFaceLandmarks(true).withFaceDescriptor()` returns
`{ detection: { score, box }, landmarks, descriptor: Float32Array(128) }`.

**Cosine similarity** (not Euclidean, not L2-normalised dot product — the
FaceRecognitionNet descriptors are not pre-normalised):

```ts
export function cosineSimilarity(a: ArrayLike<number>, b: ArrayLike<number>): number {
  if (a.length !== b.length || a.length === 0) return 0;
  let dot = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  if (normA === 0 || normB === 0) return 0;
  return dot / Math.sqrt(normA * normB);
}
```

**Observed similarity bands** (empirical, after a few hundred captures):

| Scenario | Cosine range |
|---|---|
| Same person, good lighting, similar pose | 0.75 – 0.95 |
| Same person, poor lighting / angle | 0.55 – 0.75 |
| Different people | 0.30 – 0.55 |
| Same clothing, different person (brother/sister) | up to 0.60 — risky zone |

**Threshold = 0.45** was chosen as "I'd rather make them try again than mis-mark."
Nudge up (0.50) for stricter identity; nudge down (0.40) only if the user base is
very tolerant of false matches (they shouldn't be).

**Template-set matching** (critical — don't average the enrol vectors into one):

```ts
export function topMatch(descriptor, enrolled) {
  let best = null;
  for (const face of enrolled) {
    let perFace = -Infinity;
    for (const template of face.embeddings) {     // template set, not single vector
      const sim = cosineSimilarity(descriptor, template);
      if (sim > perFace) perFace = sim;
    }
    if (perFace > (best?.similarity ?? -Infinity)) best = { face, similarity: perFace };
  }
  return best;
}
```

Why not store the mean of the 15 enrol vectors? Because a pose-averaged
descriptor is a muddy compromise — the attendance frame will be *one* pose,
and matching it against the best-matching template from the set is far more
robust than against a vector that is "slightly all three poses."

---

## 5. Code shape (frontend lifts directly — same Angular/Ionic stack)

The sister project is **Angular + Ionic** (standalone components, Signals, Angular 18+),
same as the BNI app. The seven frontend files below drop in with only trivial
renames (`member` → `employee`, chapter-specific fields removed). Server shape
is described separately in §6 — adapt that to whatever backend the sister project uses.

```
apps/web/src/app/shared/face/
  face-api.service.ts        // @Injectable({providedIn:'root'}) — ensureReady/detect/eyeAspectRatio/cropToJpeg
  matching.ts                // cosineSimilarity, topMatch, bestMatch — pure, zero-dependency
  voice.ts                   // greeting(firstName, Date) + primeSpeech + speakGreeting
  config.ts                  // the constants from §3

apps/web/src/app/core/services/
  face.service.ts            // @Injectable — HttpClient wrapper for the three endpoints

apps/web/src/app/features/<something>/
  face-register.page.ts      // standalone component, admin enrol page
  face-attendance.page.ts    // standalone component, employee self-mark page

apps/web/src/assets/face-models/   // the 6 files from §2

apps/<api>/                        // adapt to the sister team's backend stack
  routes/face.*              // POST /employees/{id}/face, DELETE, GET /face-embeddings, POST attendance/face
  services/face.*            // enrol, clear, embeddings
  core/face.*                // encode_embeddings / decode_embeddings, threshold constant
```

### 5a. Face API wrapper (copy almost verbatim)

The whole `FaceApiService` is <110 lines. The three methods your pages need:

1. `ensureReady()` — memoised model-weights loader.
2. `detect(source)` → `{ score, box, landmarks, descriptor } | null`.
3. `eyeAspectRatio(landmarks)` — EAR math for blink detection:
   ```
   EAR = (|p1-p5| + |p2-p4|) / (2 * |p0-p3|)
   // 68-point layout: left eye = points 36..41, right eye = points 42..47
   // Open eye ≈ 0.30, closed eye ≈ 0.10, blink = sharp drop + recovery
   ```
4. `cropToJpeg(video, box, side)` — crop + resize to a square, 30% padding so hair
   and chin are included, encode as JPEG for the reference photo.

Detector call used in production:
```ts
const options = new faceapi.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.5 });
const result = await faceapi.detectSingleFace(source, options)
  .withFaceLandmarks(true)
  .withFaceDescriptor();
```

### 5b. Enrol page — the state machine

```
picker → preparing → guiding → capturing → uploading → done
                                                     → error
```

Guidance states shown as a hint under the viewfinder: `noFace | multiple |
tooSmall | tooLarge | offCenter | ok`. Thresholds:
- `box.width / video.videoWidth < 0.25` → tooSmall
- `> 0.75` → tooLarge
- Centre of box outside the oval (axes 0.35 × width, 0.40 × height) → offCenter

The capture loop collects descriptors only while `score ≥ MIN_CAPTURE_SCORE
(0.75)` — low-score frames (motion blur, half-turn) are silently dropped,
preventing them from polluting the template set.

### 5c. Attendance page — the state machine

```
preparing → scanning → (blink → )? matching → success  → scanning
                                            → alreadyMarked → scanning
                                            → unknown → scanning
                     → error
```

Three guards against the "attendance marked twice" problem:
1. **Per-member cool-off**: after a match, store `now + SUPPRESSION_WINDOW_MS` in a
   `Map<memberId, number>`; skip until the window expires.
2. **Server idempotency**: server dedupes by (meetingId/employeeId, date) and
   returns `alreadyMarked: true` when the day's row already exists. UI shows a
   grey "Already marked at HH:MM" splash — just as satisfying as a green one.
3. **Attendance counter poll** every 5s so if a second device just marked the
   same person, the counter and dedupe set stay fresh.

The enrolled set is re-pulled every 120s with an `If-None-Match` header so the
server returns 304 when nothing changed (ETag = hash of `(memberId, enroledAt)`
pairs). Prevents the attendance page from ballooning when the directory grows.

---

## 6. Server-side

Three endpoints. All framework-agnostic; the BNI app uses FastAPI, but the shape
is trivial in any stack.

### 6a. Enrol — `POST /api/employees/{id}/face`
- Multipart form: `embedding` (JSON string) + `photo` (file).
- Permissions: admin / HR only (role check server-side; don't trust UI gating).
- Validates each descriptor has exactly 128 floats; rejects otherwise.
- Stores:
  - `face_embedding` (TEXT column, JSON — the full `[[128 floats], ...]` set)
  - `face_photo_file_id` (TEXT — opaque ID returned by your file storage)
  - `face_registered_at` (TEXT, server clock, ISO 8601)
- Trashes the previous reference photo if re-enrolling.

### 6b. List enrolled — `GET /api/face-embeddings`
- Returns `{ faces: [{ memberId, memberName, firstName, embeddings, photoUrl, faceRegisteredAt }], etag }`.
- `ETag: W/"faces-<16hex>"` where the hash input is `memberId|faceRegisteredAt;` joined.
  Covers add / remove / re-enrol with no missed invalidations.
- Honours `If-None-Match` for 304.
- `photoUrl` is a short-lived signed URL (BNI uses 15-min signatures via the
  file-storage layer). Never serve the raw file ID.

### 6c. Mark attendance — `POST /api/attendance/face`
- Body: `{ employeeId, confidence, lat, lng, accuracyM, city, state, country }`.
- **Validate**: `confidence >= FACE_MATCH_THRESHOLD`. Reject below the floor —
  it means the client bypassed its own threshold check and we should not trust it.
- **Server clock** for the timestamp. Never accept the client's clock for the
  attendance mark time. Client-provided `lat/lng/city` is fine, but the time of
  record is server-side.
- Dedupe per `(employeeId, dateInTenantTimezone)`; return `alreadyMarked: true`
  with the original `arrivalTime` when duplicate.
- Response: `{ employeeId, memberName, firstName, alreadyMarked, arrivalTime,
  confidence, lat, lng, city }` so the UI splash has everything.

### 6d. Clear enrolment — `DELETE /api/employees/{id}/face`
Admin only. Clears the three persisted columns and trashes the reference photo.

### 6e. Serialisation helpers (Python; adapt to your stack)

```python
FACE_DESCRIPTOR_LENGTH = 128

def encode_embeddings(vectors: list[list[float]]) -> str:
    """Store template set as JSON."""
    if not vectors:
        raise ValueError("At least one descriptor required")
    for v in vectors:
        if len(v) != FACE_DESCRIPTOR_LENGTH:
            raise ValueError(f"Descriptor must be {FACE_DESCRIPTOR_LENGTH} floats")
    return json.dumps([[float(x) for x in v] for v in vectors])

def decode_embeddings(value: str) -> list[list[float]]:
    """Handle both legacy flat-list and current template-set formats."""
    text = (value or "").strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list) or not data:
        raise ValueError("Malformed descriptor")
    if isinstance(data[0], list):
        return [[float(x) for x in v] for v in data]
    # legacy: single flat 128-length vector
    return [[float(x) for x in data]]
```

---

## 7. Geolocation + reverse geocoding (the sister project's extra ask)

### 7a. Capturing the position

Call `navigator.geolocation.getCurrentPosition` in the **same user-gesture
sequence** as the match — specifically, right before the POST, so the position
reflects "where the employee was when their face was matched," not where they
were when they loaded the page.

```ts
interface Fix { lat: number; lng: number; accuracyM: number; capturedAt: number; }

function getFix(timeoutMs = 8000): Promise<Fix> {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) {
      reject(new Error('Geolocation is not available on this device.'));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracyM: pos.coords.accuracy,
        capturedAt: pos.timestamp,
      }),
      (err) => reject(new Error(err.message || 'Could not read location.')),
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 30_000 },
    );
  });
}
```

- `enableHighAccuracy: true` — asks the OS for GPS instead of cell/wifi fix.
  On a phone, this takes 1–3s cold, <500 ms warm.
- `maximumAge: 30_000` — reuse a position fix up to 30 s old. Prevents a cold
  GPS stall on every repeated match attempt.
- `timeout: 8000` — fail gracefully if the user is indoors with no GPS. Decide
  policy (§7d) on what "no fix" means.

**Browser requirements**:
- Only works over **HTTPS** (or `http://localhost`). Secure context mandatory.
- First call shows a permission prompt. User denies → `err.code === 1`.
- iOS Safari: works, but under Settings → Safari → Location = "Ask" or "Allow".
- Android/Chrome: works. If the user is on a desktop, accuracy drops to wifi
  triangulation (~100-1000 m) — fine for city, useless for site verification.

### 7b. Reverse geocoding — pick one

| Service | Cost | Quality | Key? | Rate limit |
|---|---|---|---|---|
| **Google Maps Geocoding API** | $5 per 1k requests after free tier | Excellent | Yes | 50 QPS |
| **Mapbox Geocoding** | 100k/mo free, then $0.50/1k | Excellent | Yes | 10 QPS |
| **BigDataCloud `/reverse-geocode-client`** | Free | OK — gives city/country | **No** | Fair-use |
| **OpenStreetMap Nominatim (public)** | Free | Great | No | **1 req/sec**, must set `User-Agent` |

**Recommendation for a corporate attendance app**: BigDataCloud's free client
endpoint is the right starting point — no key, no server-side proxy, fine
accuracy for "which city." Move to Google Maps if the attendance report needs
exact address or if BigDataCloud rate-limits you under load.

Call it from the browser *after* the geolocation fix, before the attendance POST:

```ts
interface Place { city: string; state: string; country: string; }

async function reverseGeocode(lat: number, lng: number): Promise<Place> {
  // BigDataCloud: free, no key, CORS-enabled.
  const url = `https://api.bigdatacloud.net/data/reverse-geocode-client`
            + `?latitude=${lat}&longitude=${lng}&localityLanguage=en`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Reverse geocode failed (${res.status})`);
  const json = await res.json();
  return {
    city:    json.city || json.locality || json.localityInfo?.administrative?.[3]?.name || '',
    state:   json.principalSubdivision || '',
    country: json.countryName || '',
  };
}
```

**If you choose Google/Mapbox instead**: proxy the request through your own
server so the API key never ships to the browser. The server endpoint takes
`{lat, lng}` and returns `{city, state, country}`; attach standard caching
(dedupe by rounded lat/lng → 100-m granularity for 24 h is a huge saving).

### 7c. Wiring it into the attendance flow

```ts
async function matchAndMark(descriptor: Float32Array): Promise<void> {
  const match = bestMatch(descriptor, enrolled, FACE_MATCH_THRESHOLD);
  if (!match) {
    showToast({ phase: 'unknown', /* ... */ });
    return;
  }

  // Read the fix + city BEFORE the POST so the server stores the real position
  // at the moment of match (not the one from page-load).
  let fix: Fix | null = null;
  let place: Place = { city: '', state: '', country: '' };
  try {
    fix = await getFix();
    place = await reverseGeocode(fix.lat, fix.lng).catch(() => place);
  } catch (err) {
    // See §7d — decide: hard-block, or mark with location unknown.
    if (LOCATION_REQUIRED) {
      await notify.error('Location is required to mark attendance. Please enable location access.');
      phase.set('scanning');
      return;
    }
  }

  const arrival = await api.markByFace(employeeId, match.similarity, {
    lat: fix?.lat, lng: fix?.lng, accuracyM: fix?.accuracyM,
    city: place.city, state: place.state, country: place.country,
  });
  applyArrival(arrival);
}
```

### 7d. Decisions the sister project needs to make

Flag these to the user **before** the sister team's Claude Code starts coding —
they are policy choices, not technical trade-offs:

1. **Location denied → ?** Options:
   (a) Block attendance (hard requirement; shows "Enable location to continue").
   (b) Allow with `city = ""` and a flag `locationUnavailable = true` on the row.
   The default in most corporate attendance products is (a).

2. **Low-accuracy fix → ?** A desktop/wifi fix might return `accuracyM = 850`.
   If attendance must be site-anchored (within a geofence), set a threshold
   (e.g. `accuracyM <= 100`) and reject above it. If just logging city, 850 m
   is fine.

3. **Geofencing?** If the sister project wants "only count attendance if the
   employee is within X metres of the office," do the Haversine check server-side
   against a configured `(office_lat, office_lng, radiusM)` per team/branch.
   Never trust a client-reported "I am inside the geofence" flag.

4. **Server re-geocoding?** The server *can* re-run reverse-geocoding on the
   stored lat/lng as a background job to validate/canonicalise the city string.
   Nice-to-have, not critical.

5. **Audit trail**: store `{lat, lng, accuracyM, city, country, capturedAt}` as
   immutable columns on the attendance row. Don't ever update them after the
   fact — if the attendance mark is corrected, log the correction in a separate
   audit table.

---

## 8. Non-negotiables (the four things that will bite if skipped)

1. **Server clock for the attendance timestamp.** The client is allowed to
   provide lat/lng/city, but the time-of-mark is set by the server (`now()`
   at request handling). Phones lie about time.

2. **Threshold enforced on the server too.** Client posts `confidence`; server
   compares to `FACE_MATCH_THRESHOLD` and rejects below it with HTTP 422.
   The client's threshold check is UX, not security.

3. **Role-gated enrolment server-side.** Only admin / HR can POST to
   `/employees/{id}/face`. UI hiding the enrol page is not enough — permission
   check lives on the route handler.

4. **Secure context for geolocation + camera.** Both APIs require HTTPS in
   production (or `http://localhost` for dev). An HTTP site will just see the
   permission dialog never appear — budget TLS from day one.

---

## 9. Suggested implementation order

Each step is independently testable; don't skip ahead until the prior step is
working end-to-end on at least two devices.

1. **Add the three model files** to static assets and prove `ensureReady()`
   returns without network errors.
2. **Face-API wrapper + matching.ts** (pure, unit-testable — write cosine tests
   with hand-computed values first).
3. **Server endpoints + storage columns** (`face_embedding` TEXT, `face_photo_file_id`
   TEXT, `face_registered_at` TEXT on the employee row).
4. **Enrol page** — happy path only: picker → camera → 15 captures → upload. Skip
   framing hints and best-shot selection until the baseline round-trips.
5. **Add framing + best-shot selection** on the enrol page.
6. **Attendance page without blink**: fetch embeddings, scan, match, POST.
7. **Attendance page: add blink liveness** behind a tenant setting.
8. **Geolocation capture** (hard-coded blank city first; just prove lat/lng
   lands in the DB).
9. **Reverse geocoding** (BigDataCloud or your chosen provider).
10. **Policy choices** (§7d): denial handling, accuracy floor, geofence.
11. **ETag caching + 120-s refresh** on the embeddings endpoint.
12. **Audit, suppression-window, "already marked" splash**.

---

## 10. Known gotchas (saved us ~2 days collectively)

- **"Face detector returns null on a seemingly-fine video"** — the video element
  must have `readyState >= 2` and non-zero `videoWidth` before you call
  `detect()`. Loop with `setInterval`; early frames will no-op harmlessly.
- **Overlay canvas misaligned** — set `canvas.width = video.videoWidth` and
  `canvas.height = video.videoHeight` once `metadata` fires, so the detector's
  box coordinates match the canvas pixel space.
- **Selfie mirror** — set `transform: scaleX(-1)` on the `<video>` so the user's
  left is on screen-left. Do **not** mirror the canvas you draw boxes on; the
  detector sees the un-mirrored frame.
- **Web Speech on iOS Safari / older Chrome** — the first `speak()` call after
  a user gesture sometimes drops silently. Call `primeSpeech()` (speaking a
  zero-volume, one-space utterance) once during the user gesture that starts
  the camera to unlock the channel.
- **Chrome bug: `speak()` right after `cancel()` is dropped.** Don't preemptively
  cancel the queue before speaking the greeting.
- **Model weights 404 after dev-server rebuild** — some bundlers process `.bin`
  files as modules. Add an exclude rule or serve the folder as a plain static
  mount.
- **Blink gate: brief face-missing frames** — a natural blink can momentarily
  break detection. Track `missedBlinkFrames`; tolerate up to 8 before falling
  back to `scanning`.
- **getUserMedia on iOS Safari fails inside an invisible page** — only call
  `getUserMedia` after the attendance tab/page is actually in the foreground.
- **Geolocation on desktop Chrome returns a cell/wifi fix with 500+ m accuracy**.
  Decide if that's acceptable (see §7d).
- **CORS**: BigDataCloud's `/reverse-geocode-client` is CORS-enabled; their
  authenticated `/reverse-geocode` endpoint is **not**. Use the right one for
  client-side calls.

---

## 11. Files worth lifting verbatim

If the sister team has access to the BNI repo, these files are small, isolated,
and copy near-verbatim:

| BNI path | Role |
|---|---|
| `apps/web/src/app/shared/face/face-api.service.ts` | 108 lines, face-API wrapper |
| `apps/web/src/app/shared/face/matching.ts` | 73 lines, cosine + top/best match |
| `apps/web/src/app/shared/face/voice.ts` | 46 lines, greeting + speak |
| `apps/web/src/app/features/admin/members/face-register.page.ts` | ~560 lines, enrol page — rename `members` → `employees`, drop the sub-fields that aren't relevant |
| `apps/web/src/app/features/meeting/attendance/attendance-face.page.ts` | ~685 lines, attendance page — add §7's geolocation block right before the `markByFace` call |
| `apps/api/app/core/face.py` | 103 lines, constants + serialisation helpers |
| `apps/api/app/services/face_service.py` | 160 lines, enrol + embeddings-fetch service |
| `apps/api/app/api/routers/face.py` | 76 lines, three endpoints |

Everything else in the BNI face feature (chapter settings, auto-last-presenter,
substitute labels, meeting state) is **BNI-specific business logic** and should
be ignored by the sister project.

---

## 12. What to tell her Claude Code when kicking off

Paste something like this at the start of her first session:

> We're adding **face-based attendance with geolocation** to our existing
> employee-management app. There is a prior art implementation (BNI Meeting app)
> whose approach we're going to adopt — see `FACE_RECOGNITION_HANDOFF.md`.
>
> Stack: client-side face recognition via `@vladmandic/face-api` (not server-side
> ML). Three model files to self-host. 128-float descriptors, cosine similarity,
> threshold 0.45. Enrol captures 15 shots across 3 poses as a template set.
> Attendance matches against the full set, picks the max cosine, requires an
> optional blink liveness. At match time, also capture `navigator.geolocation`
> and reverse-geocode to a city — BigDataCloud's free client endpoint is fine.
> Server stamps attendance with its own clock; store lat/lng/city on the row.
>
> Please read `FACE_RECOGNITION_HANDOFF.md` end-to-end before writing code, then
> propose a plan for our stack (<name her stack>). Flag the §7d policy choices
> back to me before implementing — those are business decisions, not code decisions.

That's it — everything else is in this document.
