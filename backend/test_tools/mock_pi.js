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

if (!fs.existsSync(PENDING_DIR)) {
    fs.mkdirSync(PENDING_DIR);
}

// Ensure this matches the variable name in your .env file
const HARDWARE_SECRET = process.env.PI_SECRET;

const socket = io(SERVER_URL, {
    reconnection: true,
    auth: { token: HARDWARE_SECRET }
});

let isSystemActive = false;

console.log("Mock Pi booting up...");

socket.on("connect", () => {
    console.log("Socket Connected! ID:", socket.id);
});

socket.on("disconnect", () => {
    console.log("Socket Disconnected. Waiting for server...");
});

let lastErrorMessage = "";

socket.on("connect_error", (err) => {
    if (lastErrorMessage !== err.message) {
        console.error(`Socket Connection Error: ${err.message} (Will keep trying silently...)`);
        lastErrorMessage = err.message;
    }
});

socket.on("connect", () => {
    console.log("Socket Connected! ID:", socket.id);
    lastErrorMessage = "";
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

        // --- NEW METADATA LOGIC ---
        // Extract the exact creation time of the local file
        const stats = fs.statSync(filepath);
        const edgeTimestamp = stats.mtime.toISOString();
        const sessionId = `mock_session_${stats.mtimeMs}`; // Generate a unique session ID

        const isImage = filename.toLowerCase().endsWith('.jpg') || filename.toLowerCase().endsWith('.png');
        const fileType = isImage ? 'image' : 'video';

        form.append('sessionId', sessionId);
        form.append('fileType', fileType);
        form.append('edgeTimestamp', edgeTimestamp);
        form.append('video', fs.createReadStream(filepath));

        const res = await axios.post(`${SERVER_URL}/api/upload`, form, {
            headers: {
                ...form.getHeaders(),
                'Authorization': `Bearer ${HARDWARE_SECRET}`
            },
            maxBodyLength: Infinity,
            maxContentLength: Infinity
        });

        if (res.status === 201) {
            console.log(`[SWEEPER] Upload Success: ${filename}`);
            fs.unlinkSync(filepath);
        }
    } catch (err) {
        if (err.code === 'ECONNREFUSED') { } else {
            console.log(`[SWEEPER] Upload failed for ${filename} | Reason: ${err.message} (Will retry later)`);
            if (err.response) console.log(`Server says:`, err.response.data);
        }
    }
}

let isSweeping = false;

setInterval(async () => {
    if (isSweeping) return;

    const pendingFiles = fs.readdirSync(PENDING_DIR);
    if (pendingFiles.length > 0) {
        isSweeping = true;
        console.log(`\n[SWEEPER] Found ${pendingFiles.length} pending video(s). Attempting uploads...`);

        for (const file of pendingFiles) {
            await attemptUpload(file);
        }

        isSweeping = false;
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

    console.log("2. Recording Video locally...");
    const isoTimestamp = new Date().toISOString();
    const safeTimestamp = isoTimestamp.replace(/[:.]/g, '-');
    const newFilename = `recording_${safeTimestamp}.mp4`;
    const newFilepath = path.join(PENDING_DIR, newFilename);

    if (fs.existsSync(ASSET_SOURCE)) {
        fs.copyFileSync(ASSET_SOURCE, newFilepath);
        console.log(`Saved to SD Card: ${newFilename}`);
    } else {
        console.error("ERROR: Source asset not found.");
        return;
    }

    console.log("3. Passing to Background Uploader...");
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
rl.on('line', () => triggerMotionSequence());