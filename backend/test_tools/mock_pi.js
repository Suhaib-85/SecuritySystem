import { io } from "socket.io-client";
import readline from 'readline';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import axios from 'axios';
import FormData from 'form-data';
import dotenv from 'dotenv';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SERVER_URL = 'http://localhost:3000';
const ASSET_SOURCE = path.join(__dirname, '..', '..', 'assets', 'sample.mp4');
const PENDING_DIR = path.join(__dirname, 'pending_uploads');

if (!fs.existsSync(PENDING_DIR)) fs.mkdirSync(PENDING_DIR);

const HARDWARE_SECRET = process.env.PI_SECRET;
const socket = io(SERVER_URL, { reconnection: true, auth: { token: HARDWARE_SECRET } });

let isSystemActive = false;
console.log("Mock Pi simulation engine ready...");

socket.on("state_update", (data) => {
    isSystemActive = data.isActive;
    console.log(`\n[STATE UPDATE]: System is now ${isSystemActive ? "ARMED" : "DISARMED"}`);
});

async function attemptUpload(filename) {
    const filepath = path.join(PENDING_DIR, filename);
    try {
        const form = new FormData();
        const edgeTimestamp = new Date().toISOString(); // Native clean target
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
            fs.unlinkSync(filepath);
        }
    } catch (err) {
        console.log(`[SWEEPER] Upload failed: ${err.message}`);
    }
}

setInterval(async () => {
    const pendingFiles = fs.readdirSync(PENDING_DIR);
    if (pendingFiles.length > 0) {
        for (const file of pendingFiles) await attemptUpload(file);
    }
}, 10000);

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
    }
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
rl.on('line', () => triggerMotionSequence());