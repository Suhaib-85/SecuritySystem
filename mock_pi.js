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
const VIDEO_SOURCE = path.join(__dirname, 'assets', 'sample.mp4');

const socket = io(SERVER_URL);
let isSystemActive = false;

console.log("Mock Pi connecting...");

socket.on("connect", () => {
    console.log("Connected! ID:", socket.id);
});

socket.on("state_update", (data) => {
    isSystemActive = data.isActive;
    const status = isSystemActive ? "ARMED" : "DISARMED";
    console.log(`[STATE] ${status}`);
    if (isSystemActive) console.log("(Press ENTER to simulate Motion + Video)");
});

async function triggerMotionSequence() {
    if (!isSystemActive) {
        console.log("Motion ignored (System Disabled)");
        return;
    }

    console.log("\n--- MOTION DETECTED ---");

    console.log("1. Sending Alert...");
    socket.emit("pi_alert", { location: "Simulated Cam" });

    console.log("2. Recording Video (Simulating)...");
    await new Promise(r => setTimeout(r, 2000));

    console.log("3. Uploading Evidence...");

    if (!fs.existsSync(VIDEO_SOURCE)) {
        console.error("ERROR: 'assets/sample.mp4' not found. Cannot upload.");
        return;
    }

    try {
        const form = new FormData();
        form.append('video', fs.createReadStream(VIDEO_SOURCE));

        const res = await axios.post(`${SERVER_URL}/upload`, form, {
            headers: { ...form.getHeaders() }
        });

        console.log(`UPLOAD COMPLETE! File ID: ${res.data.fileId}`);
        console.log("--- SEQUENCE FINISHED ---\n");

    } catch (err) {
        console.error("Upload Failed:", err.message);
    }
}

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

rl.on('line', () => {
    triggerMotionSequence();
});