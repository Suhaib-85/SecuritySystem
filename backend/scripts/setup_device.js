import { setServers } from 'dns';
setServers(['8.8.8.8', '8.8.4.4']);

import mongoose from 'mongoose';
import crypto from 'crypto';
import dotenv from 'dotenv';
import Device from '../src/models/Device.js';
import { fileURLToPath } from 'url';
import path from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
dotenv.config({ path: path.join(__dirname, '../.env') });

const mongoURI = process.env.MONGO_URI;

async function provisionDevice() {
    try {
        await mongoose.connect(mongoURI);
        console.log('Connected to MongoDB');
        const deviceName = 'Raspberry_Pi_4_Main';
        const existingDevice = await Device.findOne({ deviceName });
        if (existingDevice) {
            console.log(`Device '${deviceName}' is already provisioned.`);
            process.exit(0);
        }
        const rawApiKey = crypto.randomBytes(32).toString('hex');
        const apiKeyHash = crypto.createHash('sha256').update(rawApiKey).digest('hex');
        const newDevice = new Device({
            deviceName,
            apiKeyHash,
        });
        await newDevice.save();
        console.log(`\n ✅ Device '${deviceName}' Provisiond successfully!`);
        console.log('⚠️ IMPORTANT: Please save this API key securely - you will not be able to retrieve it again!');
        console.log(`Paste this API key into the .env file on your edge device as PI_SECRET.\n RAW_API_KEY: ${rawApiKey}\n`);
    } catch (err) {
        console.error('Error:', err);
    } finally {
        process.exit(0);
    }
}

provisionDevice();
