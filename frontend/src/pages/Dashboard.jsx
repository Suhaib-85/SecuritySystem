import { useEffect, useState, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { io } from 'socket.io-client';
import { ShieldAlert, LogOut, Camera } from 'lucide-react';

export default function Dashboard() {
    const [events, setEvents] = useState([]);
    const [isActive, setIsActive] = useState(false);
    const socketRef = useRef(null); // Fixed cascading render warning loop
    const navigate = useNavigate();
    const token = localStorage.getItem('token');

    useEffect(() => {
        fetch('/api/events', { // Relative URL mapping
            headers: { 'Authorization': `Bearer ${token}` }
        })
            .then(res => {
                if (!res.ok) throw new Error('Auth failed');
                return res.json();
            })
            .then(data => setEvents(data.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))))
            .catch(() => {
                localStorage.removeItem('token');
                navigate('/login');
            });

        // Initialize connection directly into useRef memory layout
        socketRef.current = io({ auth: { token } });

        socketRef.current.on('state_update', (data) => setIsActive(data.isActive));
        socketRef.current.on('new_event', (event) => {
            setEvents(prev => [event, ...prev]);
        });

        return () => { if (socketRef.current) socketRef.current.close(); };
    }, [navigate, token]);

    const toggleSystem = () => {
        if (socketRef.current) socketRef.current.emit('toggle_system', { isActive: !isActive });
    };

    const handleLogout = () => {
        localStorage.removeItem('token');
        navigate('/login');
    };

    return (
        <div className="max-w-4xl mx-auto p-6">
            <div className="flex justify-between items-center mb-8 pb-4 border-b border-gray-700">
                <h2 className="text-2xl font-bold text-blue-400 flex items-center gap-2"><ShieldAlert /> Security Dashboard</h2>
                <button onClick={handleLogout} className="flex items-center gap-2 bg-red-600 hover:bg-red-700 px-4 py-2 rounded font-bold transition"><LogOut size={18} /> Log Out</button>
            </div>
            <div className={`p-8 rounded-xl border-2 mb-8 text-center transition-all duration-300 ${isActive ? 'border-green-500 shadow-[0_0_15px_rgba(0,255,0,0.2)]' : 'border-red-500 shadow-[0_0_15px_rgba(255,0,0,0.2)]'}`}>
                <h1 className="text-3xl font-bold mb-2">SYSTEM: {isActive ? 'ACTIVE' : 'INACTIVE'}</h1>
                <p className="text-gray-400">{isActive ? 'Monitoring for intruders...' : 'Monitoring is disabled'}</p>
            </div>
            <div className="flex gap-4 mb-8">
                <button onClick={toggleSystem} className="flex-1 bg-blue-600 hover:bg-blue-700 py-3 rounded font-bold text-lg transition">Toggle System Power</button>
                <Link to="/gallery" className="flex-1 bg-green-600 hover:bg-green-700 py-3 rounded font-bold text-lg flex items-center justify-center gap-2 transition"><Camera size={20} /> View Gallery</Link>
            </div>
            <div>
                <h3 className="text-xl font-bold mb-4">Event History</h3>
                <div className="bg-black border border-gray-800 rounded-lg overflow-hidden">
                    {events.map((event, i) => {
                        if (event.message?.includes('Uploaded') || event.message?.includes('Captured')) return null;
                        const isAlert = event.type === 'alert';
                        return (
                            <div key={event._id || i} className={`p-4 border-b border-gray-800 flex justify-between items-center ${isAlert ? 'border-l-4 border-l-red-500' : 'border-l-4 border-l-blue-500'}`}>
                                <span className={isAlert ? 'text-red-400' : 'text-blue-400'}>
                                    <strong>{new Date(event.timestamp).toLocaleTimeString()}:</strong> {event.message}
                                </span>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}