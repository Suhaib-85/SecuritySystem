import { setServers } from 'dns';
import { errorHandler } from './src/middleware/errorHandler.js';
import express from 'express';
import { createServer } from 'http';
import { Server } from 'socket.io';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';
import helmet from 'helmet';
import cors from 'cors';

import { connectDB } from './src/config/db.js';
import apiRoutes from './src/routes/api.js';
import { setupSocketLogic } from './src/socket/socketLogic.js';

dotenv.config();

// Optional DNS override — OFF by default. Set FORCE_DNS_OVERRIDE=true in .env
// only if the ISP firewall (e.g. PTCL) starts disrupting outbound Atlas traffic;
// it pins a fast public resolver (Cloudflare) to bypass ISP DNS interference.
// Must run AFTER dotenv.config() so the flag is actually loaded. Leaving it off
// uses the system/router DNS, which is faster under normal conditions.
if (process.env.FORCE_DNS_OVERRIDE === 'true') {
    setServers(['1.1.1.1', '1.0.0.1']);
    console.log('[DNS] Override active — using Cloudflare resolvers (1.1.1.1).');
}
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const httpServer = createServer(app);

const io = new Server(httpServer, {
    cors: { origin: ["http://localhost:5173", "http://localhost:3000"] }
});

app.use(helmet({
    contentSecurityPolicy: {
        directives: {
            defaultSrc: ["'self'"],
            scriptSrc: ["'self'", "'unsafe-inline'"], // Required for React
            styleSrc: ["'self'", "'unsafe-inline'"],  // Required for Tailwind
            imgSrc: ["'self'", "data:", "blob:"],
            connectSrc: ["'self'"],
            mediaSrc: ["'self'", "blob:"],            // Required for WebM playback
        }
    }
}));

app.use(cors({
    origin: ["http://localhost:3000", "http://localhost:5173"],
    methods: ["GET", "POST"],
    credentials: true
}));

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

app.set('socketio', io);

app.use('/api', apiRoutes);

app.get(/.*/, (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.use(errorHandler);

const startServer = async () => {
    try {
        await connectDB();

        setupSocketLogic(io);

        const PORT = process.env.PORT || 3000;
        httpServer.listen(PORT, () => {
            console.log(`Server Securely Running: http://localhost:${PORT}`);
        });
    } catch (err) {
        console.error("FATAL ERROR: Server failed to start:", err);
        process.exit(1);
    }
};

startServer();