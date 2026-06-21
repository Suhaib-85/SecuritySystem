import { io } from "socket.io-client";
import readline from 'readline';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import axios from 'axios';
import FormData from 'form-data';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

dotenv.config({ path: path.join(__dirname, '..', '.env') });

// ----------------------------------------------------------------------------
// CONFIG — mirrors edge_device/main.py so the simulator exercises the SAME
// upload/queue/retry pipeline as the real device. The ONLY difference between
// this file and main.py is the source of the "captured" media:
//   - main.py     : real laptop camera + live person-detection inference
//   - mock_pi.js  : copies assets/sample.mp4 (no camera, no inference)
// Everything downstream (queue, sweeper, retries, sockets) is identical.
// ----------------------------------------------------------------------------
const SERVER_URL = 'http://localhost:3000';
const ASSET_SOURCE = path.join(__dirname, '..', '..', 'assets', 'sample.mp4');
const PENDING_DIR = path.join(__dirname, 'pending_uploads');
const HARDWARE_SECRET = process.env.PI_SECRET;

const MAX_UPLOAD_LIMIT = 5;        // give up after 5 genuine server REJECTIONS (matches main.py)
const SWEEP_INTERVAL_MS = 10000;   // 10s poll — matches main.py's sweeper_loop sleep
const UPLOAD_TIMEOUT_MS = 120000;  // 120s per-upload ceiling (matches main.py requests timeout)

if (!fs.existsSync(PENDING_DIR)) fs.mkdirSync(PENDING_DIR, { recursive: true });

// --- SHARED STATE (mirrors main.py globals) ---
let isSystemActive = false;
let isSweeping = false;
const pendingUploads = [];   // in-memory queue of metadata objects (like main.py pending_uploads)
const missedAlerts = [];     // alerts emitted while the socket was down; flushed on reconnect

const socket = io(SERVER_URL, { reconnection: true, auth: { token: HARDWARE_SECRET } });

console.log("Mock Pi simulation engine ready... Press ENTER to simulate a motion event.");

// ----------------------------------------------------------------------------
// SOCKET LAYER (mirrors main.py connect/disconnect/state_update handlers)
// ----------------------------------------------------------------------------
socket.on("connect", () => {
    console.log(`\n✅ NETWORK: Connected successfully. Session ID: ${socket.id}`);
    socket.emit("register_pi", { token: HARDWARE_SECRET });

    // Flush any alerts captured during a network dropout
    for (const alert of missedAlerts) socket.emit("pi_alert", alert);
    missedAlerts.length = 0;
});

socket.on("disconnect", () => {
    console.log("\n❌ NETWORK: Dropped connection interface. Re-establishing channel...");
});

socket.on("state_update", (data) => {
    isSystemActive = data.isActive;
    console.log(`🔄 STATE: System telemetry synchronized to: ${isSystemActive ? "ARMED 🔴" : "DISARMED 🟢"}`);
});

// ----------------------------------------------------------------------------
// HELPERS
// ----------------------------------------------------------------------------
function safeUnlink(p) {
    try { if (fs.existsSync(p)) fs.unlinkSync(p); } catch { /* best-effort cleanup */ }
}

function removeFromQueue(pendingFile) {
    const idx = pendingUploads.indexOf(pendingFile);
    if (idx !== -1) pendingUploads.splice(idx, 1);
}

// Unique, chronologically-sortable session id mirroring main.py: millisecond
// resolution (YYYYMMDD_HHMMSS_mmm) plus a same-millisecond guard, so two events
// triggered within the same millisecond can never share a filename.
let lastSessionStamp = '';
let sessionDedupeCounter = 0;

function makeSessionId() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    const ms = String(d.getMilliseconds()).padStart(3, '0');
    const stamp = `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}_${ms}`;

    if (stamp === lastSessionStamp) {
        sessionDedupeCounter += 1;
        return `${stamp}_${sessionDedupeCounter}`;
    }
    lastSessionStamp = stamp;
    sessionDedupeCounter = 0;
    return stamp;
}

// ----------------------------------------------------------------------------
// QUEUE: stage a captured asset for upload (mirrors main.py save_to_pending)
// ----------------------------------------------------------------------------
function saveToPending(srcPath, fileType, sessionId) {
    try {
        const filename = `evidence_${sessionId}_clip.mp4`;
        const pendingPath = path.join(PENDING_DIR, filename);
        fs.copyFileSync(srcPath, pendingPath);

        const metadata = {
            filename,
            filepath: pendingPath,
            type: fileType,
            attempts: 0,
            sessionId,
            timestamp: new Date().toISOString(),
        };
        fs.writeFileSync(`${pendingPath}.json`, JSON.stringify(metadata));

        pendingUploads.push(metadata);
        console.log(`📁 STORAGE: Asset compiled and indexed for queue execution: ${filename}`);
        return true;
    } catch (e) {
        console.log(`❌ STORAGE ERROR: Failed to stage asset for transmission: ${e.message}`);
        return false;
    }
}

// ----------------------------------------------------------------------------
// UPLOAD: one transmission attempt (mirrors main.py attempt_upload)
//   - 201            -> success: delete file + sidecar, drop from queue
//   - other HTTP code-> genuine rejection: burn an attempt; drop after MAX_UPLOAD_LIMIT
//   - network/timeout-> leave file untouched, retry next sweep (attempt NOT burned)
// validateStatus:()=>true makes axios behave like Python requests (no throw on non-2xx).
// ----------------------------------------------------------------------------
async function attemptUpload(pendingFile) {
    try {
        const form = new FormData();
        form.append('sessionId', pendingFile.sessionId || 'unknown');
        form.append('fileType', pendingFile.type);
        form.append('edgeTimestamp', pendingFile.timestamp);
        form.append('video', fs.createReadStream(pendingFile.filepath));

        const res = await axios.post(`${SERVER_URL}/api/upload`, form, {
            headers: { ...form.getHeaders(), 'Authorization': `Bearer ${HARDWARE_SECRET}` },
            maxContentLength: Infinity,
            maxBodyLength: Infinity,
            timeout: UPLOAD_TIMEOUT_MS,
            validateStatus: () => true,
        });

        pendingFile.attempts += 1;
        const jsonPath = `${pendingFile.filepath}.json`;

        if (res.status === 201) {
            console.log(`🧹 SWEEPER: Transaction clear. Uploaded successfully: ${pendingFile.filename}`);
            safeUnlink(pendingFile.filepath);
            safeUnlink(jsonPath);
            removeFromQueue(pendingFile);
            return true;
        }

        const serverError = res.data?.error ? `-> ${res.data.error}` : '';
        console.log(`[SWEEPER] Upload rejected (Status: ${res.status}) ${serverError}`);

        if (pendingFile.attempts >= MAX_UPLOAD_LIMIT) {
            console.log(`❌ SWEEPER: Boundary limit dropped. Dropping corrupted package: ${pendingFile.filename}`);
            safeUnlink(pendingFile.filepath);
            safeUnlink(jsonPath);
            removeFromQueue(pendingFile);
        }
        return false;
    } catch (err) {
        if (err.code === 'ENOENT' || (err.message && err.message.includes('ENOENT'))) {
            // The media file is gone (already uploaded, or removed externally).
            // Nothing to retry — drop this stale queue entry + its sidecar instead
            // of mislabelling it as a network failure and retrying forever.
            console.log(`⚠️  SWEEPER: File no longer on disk for ${pendingFile.filename} — dropping stale queue entry.`);
            safeUnlink(`${pendingFile.filepath}.json`);
            removeFromQueue(pendingFile);
            return false;
        }
        // Genuine network/timeout failure: DON'T burn an attempt — keep the file
        // and retry on the next sweep, so a temporarily-down server never loses evidence.
        console.log(`❌ SWEEPER TRANSMISSION FAILED: Network channel blocked: ${err.message}`);
        return false;
    }
}

// ----------------------------------------------------------------------------
// SWEEPER: drain the queue once per cycle (mirrors main.py sweeper_function)
// The isSweeping guard prevents overlapping cycles; the fixed interval below
// guarantees zero CPU spin even when every upload is failing.
// ----------------------------------------------------------------------------
async function sweeperFunction() {
    if (isSweeping || pendingUploads.length === 0) return;
    isSweeping = true;
    try {
        const snapshot = [...pendingUploads]; // oldest-first; new triggers append to the end
        for (const pendingFile of snapshot) {
            await attemptUpload(pendingFile);
        }
    } finally {
        isSweeping = false;
    }
}

function startSweeper() {
    setInterval(() => { sweeperFunction(); }, SWEEP_INTERVAL_MS);
}

// ----------------------------------------------------------------------------
// RECOVERY: rebuild the queue from sidecars on startup (mirrors main.py
// bootstrap_pending). Disk is the source of truth: any clip left in
// PENDING_DIR from a previous run is re-queued from its {name}.json sidecar so
// it eventually reaches the database. Media whose sidecar is missing/corrupt
// can't be uploaded safely, so it's discarded to avoid disk bloat. Orphaned
// sidecars (no media) are cleaned up too.
// ----------------------------------------------------------------------------
function bootstrapPending() {
    let recovered = 0;
    let entries;
    try {
        entries = fs.readdirSync(PENDING_DIR);
    } catch {
        return;
    }

    const mediaFiles = entries.filter(f => !f.endsWith('.json') && !f.startsWith('.'));

    for (const filename of mediaFiles) {
        const filepath = path.join(PENDING_DIR, filename);
        const jsonPath = `${filepath}.json`;

        if (!fs.existsSync(jsonPath)) {
            console.log(`🗑️  RECOVERY: No sidecar for ${filename} — discarding orphan.`);
            safeUnlink(filepath);
            continue;
        }

        try {
            const meta = JSON.parse(fs.readFileSync(jsonPath, 'utf-8'));
            // Reset attempts; preserve sessionId/type/timestamp so the Gallery
            // groups + orders the clip by its ORIGINAL capture time.
            pendingUploads.push({
                filename,
                filepath,
                type: meta.type || 'video',
                attempts: 0,
                sessionId: meta.sessionId || 'unknown',
                timestamp: meta.timestamp || new Date().toISOString(),
            });
            recovered += 1;
        } catch (e) {
            console.log(`🗑️  RECOVERY: Corrupt sidecar for ${filename} (${e.message}) — discarding orphan.`);
            safeUnlink(filepath);
            safeUnlink(jsonPath);
        }
    }

    // Sweep up any orphaned sidecars whose media file is gone.
    for (const filename of fs.readdirSync(PENDING_DIR).filter(f => f.endsWith('.json'))) {
        const jsonPath = path.join(PENDING_DIR, filename);
        if (!fs.existsSync(jsonPath.slice(0, -5))) safeUnlink(jsonPath);
    }

    if (recovered) {
        console.log(`🔁 RECOVERY: Re-queued ${recovered} un-uploaded asset(s) from a previous session.`);
    }
}

// ----------------------------------------------------------------------------
// MOTION TRIGGER: the mock's stand-in for a real detection event.
// Emits the alert + stages sample.mp4 — identical downstream path to main.py.
// ----------------------------------------------------------------------------
function triggerMotionSequence() {
    if (!isSystemActive) {
        console.log("Motion ignored (System Disarmed)");
        return;
    }

    const sessionId = makeSessionId();
    const alertPayload = {
        message: "Motion Detected: Intruder Alert",
        location: "Simulated Web Camera",
        sessionId,
    };

    if (socket.connected) {
        socket.emit("pi_alert", alertPayload);
    } else {
        missedAlerts.push(alertPayload); // cache and flush on reconnect (mirrors main.py)
    }

    if (fs.existsSync(ASSET_SOURCE)) {
        saveToPending(ASSET_SOURCE, "video", sessionId);
    } else {
        console.log(`❌ ASSET MISSING: sample.mp4 not found at ${ASSET_SOURCE}`);
    }
}

// --- BOOTSTRAP ---
bootstrapPending();
startSweeper();
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
rl.on('line', () => triggerMotionSequence());
