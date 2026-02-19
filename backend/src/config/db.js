import mongoose from 'mongoose';
import { GridFSBucket } from 'mongodb';

let gfsBucket;

export const connectDB = async () => {
    try {
        const conn = await mongoose.connect(process.env.MONGO_URI);
        console.log(`MongoDB Connected: ${conn.connection.host}`);

        const db = mongoose.connection.db;
        gfsBucket = new GridFSBucket(db, { bucketName: 'uploads' });
        return gfsBucket;
    } catch (err) {
        console.error(`Error: ${err.message}`);
        process.exit(1);
    }
};

export { gfsBucket };