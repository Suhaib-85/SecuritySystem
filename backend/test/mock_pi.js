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

const SERVER_URL = 'http://localhost:3000';
const ASSET_SOURCE = path.join(__dirname, '..', '..', 'assets', 'sample.mp4');
const PENDING_DIR = path.join(__dirname, 'pending_uploads');

if (!fs.existsSync(PENDING_DIR)) fs.mkdirSync(PENDING_DIR, { recursive: true });

const HARDWARE_SECRET = process.env.PI_SECRET;
const socket = io(SERVER_URL, { reconnection: true, auth: { token: HARDWARE_SECRET } });

let isSystemActive = false;
console.log("Mock Pi simulation engine ready...");

const activeUploads = new Set();
let isProcessingQueue = false; // Prevents worker race conditions

socket.on("state_update", (data) => {
    isSystemActive = data.isActive;
    console.log(`\n[STATE UPDATE]: System is now ${isSystemActive ? "ARMED" : "DISARMED"}`);
});

async function attemptUpload(filename) {
    const filepath = path.join(PENDING_DIR, filename);
    try {
        const form = new FormData();
        const edgeTimestamp = new Date().toISOString();
        const sessionId = filename.includes('___') ? filename.split('___')[0] : `mock_${Date.now()}`;
        const fileType = filename.toLowerCase().endsWith('.jpg') ? 'image' : 'video';

        form.append('sessionId', sessionId);
        form.append('fileType', fileType);
        form.append('edgeTimestamp', edgeTimestamp);
        form.append('video', fs.createReadStream(filepath));

        const res = await axios.post(`${SERVER_URL}/api/upload`, form, {
            headers: { ...form.getHeaders(), 'Authorization': `Bearer ${HARDWARE_SECRET}` }
        });

        if (res.status === 201) {
            console.log(`[SWEEPER] Uploaded Successfully: ${filename}`);
            if (fs.existsSync(filepath)) fs.unlinkSync(filepath);
        }
    } catch (err) {
        const status = err.response?.status ? `(Status: ${err.response.status})` : '';
        const serverError = err.response?.data?.error ? `-> ${err.response.data.error}` : '';
        console.log(`[SWEEPER] Upload failed ${status}: ${err.message} ${serverError}`);
    } finally {
        activeUploads.delete(filename);
    }
}

// 🚀 REACTIVE QUEUE WORKER (No Timers, Zero CPU Overhead)
async function wakeSweeper() {
    if (isProcessingQueue) return; // Guard clause: if the worker is already running, do nothing
    isProcessingQueue = true;

    try {
        let files = fs.readdirSync(PENDING_DIR);
        let pendingFiles = files.filter(file => !file.startsWith('.') && !activeUploads.has(file));

        // Keep draining the directory until no deployable assets remain
        while (pendingFiles.length > 0) {
            for (const file of pendingFiles) {
                console.log(`⚡ Instant lock & transmit initiated for: ${file}`);
                activeUploads.add(file);
                await attemptUpload(file);
            }
            // Re-read directory to verify if new files landed while we were uploading
            files = fs.readdirSync(PENDING_DIR);
            pendingFiles = files.filter(file => !file.startsWith('.') && !activeUploads.has(file));
        }
    } catch (err) {
        console.log(`[WORKER ERROR]: ${err.message}`);
    } finally {
        isProcessingQueue = false; // Put worker back to sleep
    }
}

async function triggerMotionSequence() {
    if (!isSystemActive) return console.log("Motion ignored (System Disarmed)");
    const currentSessionId = `mock_session_${Date.now()}`;

    if (socket.connected) {
        socket.emit("pi_alert", { location: "Simulated Web Camera", sessionId: currentSessionId });
    }

    const newFilename = `${currentSessionId}___clip.mp4`;
    if (fs.existsSync(ASSET_SOURCE)) {
        fs.copyFileSync(ASSET_SOURCE, path.join(PENDING_DIR, newFilename));
        console.log(`Saved virtual capture: ${newFilename}`);

        // 🔥 SIGNAL WAKEUP: Trigger processing the exact millisecond the file copy completes
        wakeSweeper();
    }
}

// 🏁 BOOTSTRAP: Clear out backlog immediately on startup, then sit at 0% CPU
wakeSweeper();

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
rl.on('line', () => triggerMotionSequence());