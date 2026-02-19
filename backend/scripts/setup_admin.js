import { setServers } from 'dns';
setServers(['8.8.8.8', '8.8.4.4']);

import mongoose from 'mongoose';
import bcrypt from 'bcrypt';
import dotenv from 'dotenv';

dotenv.config();

const mongoURI = process.env.MONGO_URI;

const AdminSchema = new mongoose.Schema({
    username: { type: String, required: true, unique: true },
    password: { type: String, required: true }
});
const Admin = mongoose.model('Admin', AdminSchema);

async function createAdmin() {
    try {
        await mongoose.connect(mongoURI);
        console.log('Connected to MongoDB');

        const existingAdmin = await Admin.findOne({ username: 'admin' });
        if (existingAdmin) {
            console.log('Admin already exists! Exiting.');
            process.exit(0);
        }

        const salt = await bcrypt.genSalt(10);

        const setupPassword = process.env.ADMIN_SETUP_PASSWORD;
        if (!setupPassword) {
            console.error("Error: ADMIN_SETUP_PASSWORD not found in .env");
            process.exit(1);
        }

        const hashedPassword = await bcrypt.hash(setupPassword, salt);

        const newAdmin = new Admin({
            username: 'admin',
            password: hashedPassword
        });

        await newAdmin.save();
        console.log('Admin account created successfully!');

    } catch (err) {
        console.error('Error:', err);
    } finally {
        process.exit(0);
    }
}

createAdmin();