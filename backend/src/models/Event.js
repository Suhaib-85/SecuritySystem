import mongoose from 'mongoose';

const eventSchema = new mongoose.Schema({
    timestamp: { type: Date, default: Date.now },
    type: { type: String, required: true },
    message: { type: String, required: true },
    deviceId: { type: String, required: true, default: 'pi_camera_front' },
    severity: { type: String, enum: ['info', 'warning', 'alert'], default: 'info' },
    status: { type: String, enum: ['new', 'reviewed', 'archived'], default: 'new' },
    videoId: { type: mongoose.Schema.Types.ObjectId, ref: 'fs.files' },
    filename: String,
    sessionId: String,
    fileType: String
});

export default mongoose.model('Event', eventSchema);