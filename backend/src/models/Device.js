import mongoose from 'mongoose';

const deviceSchema = new mongoose.Schema({
    deviceId: { type: String, required: true, unique: true },
    deviceName: { type: String, required: true, unique: true },
    apiKeyHash: { type: String, required: true },
    isActive: { type: Boolean, default: true },
    createdAt: { type: Date, default: Date.now }
});

export default mongoose.model('Device', deviceSchema);