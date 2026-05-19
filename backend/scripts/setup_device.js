import { setServers } from 'dns';
setServers(['8.8.8.8', '8.8.4.4']);

import mongoose from 'mongoose';
import crypto from 'crypto';
import dotenv from 'dotenv';
import Device from '../src/models/Device.js';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '../.env') });

async function provisionDevice() {
    try {
        await mongoose.connect(process.env.MONGO_URI);
        console.log('Connected to MongoDB ecosystem.');

        const deviceName = 'Raspberry_Pi_5_Main_Core'; // Accurate architecture descriptor
        const existingDevice = await Device.findOne({ deviceName });

        if (existingDevice) {
            console.log(`Device '${deviceName}' holds pre-existing provisioning maps.`);
            process.exit(0);
        }

        const rawApiKey = crypto.randomBytes(32).toString('hex');
        const apiKeyHash = crypto.createHash('sha256').update(rawApiKey).digest('hex');

        const newDevice = new Device({ deviceId: 'pi_camera_front', deviceName, apiKeyHash });
        await newDevice.save();

        console.log(`\n ✅ Device '${deviceName}' Provisioned successfully!`);
        console.log(`RAW_API_KEY: ${rawApiKey}\n`);
        process.exit(0);
    } catch (err) {
        console.error('Fatal terminal configuration drop error:', err);
        process.exit(1); // Standard shell failure flag
    }
}
provisionDevice();