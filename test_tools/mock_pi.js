import { io } from "socket.io-client";
import readline from 'readline';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import axios from 'axios';
import FormData from 'form-data';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const SERVER_URL = 'http://localhost:3000';
const ASSET_SOURCE = path.join(__dirname, '..', 'assets', 'sample.mp4');
const PENDING_DIR = path.join(__dirname, 'pending_uploads');

if (!fs.existsSync(PENDING_DIR)) {
    fs.mkdirSync(PENDING_DIR);
}

const socket = io(SERVER_URL, { reconnection: true });
let isSystemActive = false;

console.log("Mock Pi booting up...");

socket.on("connect", () => {
    console.log("Socket Connected! ID:", socket.id);
});

socket.on("disconnect", () => {
    console.log("Socket Disconnected. Waiting for server...");
});

socket.on("state_update", (data) => {
    isSystemActive = data.isActive;
    const status = isSystemActive ? "ARMED" : "DISARMED";
    console.log(`\n[STATE] ${status}`);
    if (isSystemActive) console.log("   (Press ENTER to simulate Motion)");
});

async function attemptUpload(filename) {
    const filepath = path.join(PENDING_DIR, filename);

    try {
        const form = new FormData();
        form.append('video', fs.createReadStream(filepath));

        const res = await axios.post(`${SERVER_URL}/upload`, form, {
            headers: { ...form.getHeaders() },
            maxBodyLength: Infinity,
            maxContentLength: Infinity
        });

        if (res.status === 201) {
            console.log(`[SWEEPER] Upload Success: ${filename}`);
            fs.unlinkSync(filepath);
        }
    } catch (err) {
        if (err.code === 'ECONNREFUSED') {
        } else {
            console.log(`[SWEEPER] Upload failed for ${filename} (Will retry later)`);
        }
    }
}

setInterval(() => {
    const pendingFiles = fs.readdirSync(PENDING_DIR);
    if (pendingFiles.length > 0) {
        console.log(`\n🔄 [SWEEPER] Found ${pendingFiles.length} pending video(s). Attempting uploads...`);
        pendingFiles.forEach(file => attemptUpload(file));
    }
}, 10000);


async function triggerMotionSequence() {
    if (!isSystemActive) {
        console.log("Motion ignored (System Disabled)");
        return;
    }

    console.log("\n--- MOTION DETECTED ---");

    console.log("1. Sending Instant Alert...");
    if (socket.connected) {
        socket.emit("pi_alert", { location: "Simulated Cam" });
    } else {
        console.log("Alert dropped (No socket connection)");
    }

    // B. Simulate Recording Video
    console.log("2. Recording Video locally...");
    const timestamp = Date.now();
    const newFilename = `recording_${timestamp}.mp4`;
    const newFilepath = path.join(PENDING_DIR, newFilename);

    if (fs.existsSync(ASSET_SOURCE)) {
        fs.copyFileSync(ASSET_SOURCE, newFilepath);
        console.log(`Saved to SD Card: ${newFilename}`);
    } else {
        console.error("ERROR: Source asset not found.");
        return;
    }

    console.log("3. Passing to Background Uploader...");
    attemptUpload(newFilename);
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
rl.on('line', () => triggerMotionSequence());