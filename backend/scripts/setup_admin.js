import { setServers } from 'dns';
setServers(['8.8.8.8', '8.8.4.4']);
import mongoose from 'mongoose';
import bcrypt from 'bcrypt';
import dotenv from 'dotenv';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: path.join(__dirname, '../.env') });

const AdminSchema = new mongoose.Schema({
    username: { type: String, required: true, unique: true },
    password: { type: String, required: true }
});
const Admin = mongoose.model('Admin', AdminSchema);

async function createAdmin() {
    try {
        await mongoose.connect(process.env.MONGO_URI);
        const existingAdmin = await Admin.findOne({ username: 'admin' });
        if (existingAdmin) {
            console.log('Administrative account already structurally present.');
            process.exit(0);
        }

        const setupPassword = process.env.ADMIN_SETUP_PASSWORD;
        if (!setupPassword) {
            console.error("Initialization configuration value ADMIN_SETUP_PASSWORD missing.");
            process.exit(1);
        }

        const hashedPassword = await bcrypt.hash(setupPassword, 10);
        const newAdmin = new Admin({ username: 'admin', password: hashedPassword });
        await newAdmin.save();

        console.log('Administrative identity verified and saved.');
        process.exit(0);
    } catch (err) {
        console.error('Fatal admin generation drop configuration error:', err);
        process.exit(1);
    }
}
createAdmin();