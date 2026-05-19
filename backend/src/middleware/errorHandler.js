// backend/src/middleware/errorHandler.js

export const errorHandler = (err, req, res, next) => {
    // Log the error for the backend terminal
    console.error(`❌ [SERVER ERROR]: ${err.message}`);

    // If a specific status code was already set (like 400 or 401), keep it. 
    // Otherwise, default to a 500 Internal Server Error.
    const statusCode = res.statusCode === 200 ? 500 : res.statusCode;

    res.status(statusCode).json({
        error: err.message,
        // Optional: Hide the stack trace in production so attackers don't see your folder structure
        stack: process.env.NODE_ENV === 'production' ? null : err.stack
    });
};