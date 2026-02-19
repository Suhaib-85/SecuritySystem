import { setServers } from 'dns';
setServers(['8.8.8.8', '8.8.4.4']);

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
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const httpServer = createServer(app);

const io = new Server(httpServer, {
    cors: { origin: "*" }
});

app.use(helmet({ contentSecurityPolicy: false }));
app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

app.set('socketio', io);

app.use('/api', apiRoutes);

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