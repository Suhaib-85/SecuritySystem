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

        const targetDeviceId = 'pi_camera_front';
        const deviceName = 'Raspberry_Pi_5_Main_Core';

        // 1. Generate the fresh cryptographic key details first
        const rawApiKey = crypto.randomBytes(32).toString('hex');
        const apiKeyHash = crypto.createHash('sha256').update(rawApiKey).digest('hex');

        // 2. Use an upsert configuration to find and overwrite the key safely
        const device = await Device.findOneAndUpdate(
            { deviceId: targetDeviceId },
            {
                $set: {
                    deviceName,
                    apiKeyHash
                }
            },
            { upsert: true, returnDocument: 'after', runValidators: true }
        );

        console.log(`\n ✅ Device '${device.deviceName}' successfully provisioned/rotated!`);
        console.log(`⚙️  Target Device ID: ${device.deviceId}`);
        console.log(`🔑 NEW RAW_API_KEY: ${rawApiKey}\n`);
        console.log(`⚠️  Make sure to update your .env files with this new value!`);

        process.exit(0);
    } catch (err) {
        console.error('Fatal terminal configuration drop error:', err);
        process.exit(1);
    }
}
provisionDevice();