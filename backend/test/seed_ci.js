// backend/test/seed_ci.js
//
// Seeds the ephemeral CI MongoDB with the one device the test suite needs.
//
// A fresh throwaway database (spun up as a GitHub Actions service container) has
// no provisioned device, so an upload carrying `Bearer <PI_SECRET>` would fail
// auth (no matching Device.apiKeyHash) and Profile 3 would get a 400 instead of
// a 201. This inserts a device whose apiKeyHash == sha256(PI_SECRET), exactly
// how setup_device.js / verifyToken compute it, so the Bearer token validates.
//
// Idempotent (upsert), and safe to run against any Mongo — but it is intended
// ONLY for the disposable CI database, never your real Atlas cluster.

import mongoose from 'mongoose';
import crypto from 'crypto';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';
import Device from '../src/models/Device.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// Local convenience only; in CI the workflow's env vars are already set and
// take precedence (dotenv does not override existing process.env values).
dotenv.config({ path: path.join(__dirname, '..', '.env') });

async function seed() {
    const uri = process.env.MONGO_URI;
    // Mirror test_suite.js's fallback EXACTLY. If PI_SECRET isn't present, both
    // the seed and the test converge on this same literal, so the device the
    // seed stores always matches the token the upload test sends. (MONGO_URI is
    // still required — without it there's nowhere to seed.)
    const piSecret = process.env.PI_SECRET || 'ci_test_secret_key_123';

    if (!uri) {
        console.error('seed_ci: MONGO_URI must be set.');
        process.exit(1);
    }

    try {
        await mongoose.connect(uri);
        const apiKeyHash = crypto.createHash('sha256').update(piSecret).digest('hex');

        await Device.findOneAndUpdate(
            { deviceId: 'pi_camera_front' },
            { $set: { deviceName: 'CI_Test_Device', apiKeyHash, isActive: true } },
            { upsert: true, returnDocument: 'after' }
        );

        console.log("seed_ci: device 'pi_camera_front' seeded into ephemeral test DB.");
        await mongoose.disconnect();
        process.exit(0);
    } catch (err) {
        console.error('seed_ci: seeding failed:', err.message);
        process.exit(1);
    }
}

seed();
