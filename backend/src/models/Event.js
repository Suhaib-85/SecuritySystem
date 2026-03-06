import mongoose from 'mongoose';

const eventSchema = new mongoose.Schema({
    timestamp: { type: Date, default: Date.now },
    type: { type: String, required: true },
    message: String,
    videoId: mongoose.Schema.Types.ObjectId,
    filename: String,
    sessionId: String,
    fileType: String
});

export default mongoose.model('Event', eventSchema);