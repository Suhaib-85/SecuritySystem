import { useEffect, useState, useMemo, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, Video, X as CloseIcon, Trash2 } from 'lucide-react';

export default function Gallery() {
    const [allEvents, setAllEvents] = useState([]);
    const [activeMedia, setActiveMedia] = useState(null);
    const [filterStatus, setFilterStatus] = useState('all');
    const [filterSeverity, setFilterSeverity] = useState('all');
    const [filterDevice, setFilterDevice] = useState('all');

    const navigate = useNavigate();
    const token = localStorage.getItem('token');

    const fetchEvents = useCallback(async () => {
        try {
            const res = await fetch(`/api/events`, { headers: { 'Authorization': `Bearer ${token}` } });
            if (!res.ok) throw new Error('Auth failed');
            const data = await res.json();
            setAllEvents(data);
        } catch (err) {
            console.error("Gallery event stream connection trace drop:", err); // This satisfies ESLint!
            navigate('/login');
        }
    }, [token, navigate]);

    const handleStatusChange = async (id, newStatus) => {
        try {
            const res = await fetch(`/api/events/${id}/status`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                body: JSON.stringify({ status: newStatus })
            });
            if (res.ok) {
                setAllEvents(prev => prev.map(ev => ev._id === id ? { ...ev, status: newStatus } : ev));
            }
        } catch (error) {
            console.error("Failed to update status", error);
        }
    };

    const handleDelete = async (id) => {
        if (!window.confirm("Delete this evidence?")) return;
        try {
            const res = await fetch(`/api/events/${id}`, { method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` } });
            if (res.ok) setAllEvents(prev => prev.filter(ev => ev._id !== id));
        } catch (error) {
            console.error("Failed to delete event", error);
        }
    };

    const sessions = useMemo(() => {
        const filtered = allEvents.filter(event => {
            if (!event.videoId) return false;
            const matchesStatus = filterStatus === 'all' || event.status === filterStatus;
            const matchesSeverity = filterSeverity === 'all' || event.severity === filterSeverity;
            const matchesDevice = filterDevice === 'all' || event.deviceId === filterDevice;
            return matchesStatus && matchesSeverity && matchesDevice;
        });

        const grouped = {};
        filtered.forEach(event => {
            const sid = event.sessionId || 'unknown';
            if (!grouped[sid]) {
                grouped[sid] = { id: sid, events: [], timestamp: event.timestamp };
            }
            grouped[sid].events.push(event);
        });

        return Object.values(grouped).sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    }, [allEvents, filterStatus, filterSeverity, filterDevice]);

    useEffect(() => { fetchEvents(); }, [fetchEvents]);
    const uniqueDevices = [...new Set(allEvents.map(e => e.deviceId).filter(Boolean))];

    return (
        <div className="max-w-7xl mx-auto p-6">
            <div className="flex justify-between items-center mb-6 pb-4 border-b border-gray-700">
                <h2 className="text-2xl font-bold text-blue-400">📸 Evidence Gallery</h2>
                <Link to="/" className="flex items-center gap-2 bg-gray-800 hover:bg-gray-700 px-4 py-2 rounded font-bold transition"><ArrowLeft size={18} /> Back</Link>
            </div>

            <div className="flex flex-wrap gap-4 mb-8 bg-gray-800/50 p-4 rounded-xl border border-gray-700">
                <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className="bg-gray-900 text-white p-2 rounded">
                    <option value="all">All Statuses</option>
                    <option value="new">🔴 New</option>
                    <option value="reviewed">✅ Reviewed</option>
                    <option value="archived">📦 Archived</option>
                </select>
                <select value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)} className="bg-gray-900 text-white p-2 rounded">
                    <option value="all">All Severities</option>
                    <option value="alert">🚨 Alerts</option>
                    <option value="warning">⚠️ Warnings</option>
                </select>
                <select value={filterDevice} onChange={(e) => setFilterDevice(e.target.value)} className="bg-gray-900 text-white p-2 rounded">
                    <option value="all">All Devices</option>
                    {uniqueDevices.map(d => <option key={d} value={d}>{d}</option>)}
                </select>
            </div>

            {sessions.length === 0 && <div className="text-center text-gray-500 mt-20">No matching evidence captured.</div>}

            {sessions.map(session => (
                <div key={session.id} className="mb-10 bg-gray-800 rounded-xl p-6 border border-gray-700 shadow-lg">
                    <div className="mb-4 pb-2 border-b border-gray-700">
                        {/* Elegant Consumer-Grade Event Card Headers */}
                        <h3 className="text-xl font-bold text-red-400">🔴 Intrusion Detected</h3>
                        <span className="text-gray-400 text-sm font-semibold">
                            {new Date(session.timestamp).toLocaleDateString(undefined, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })} • {new Date(session.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-6">
                        {session.events.map(event => (
                            <div key={event._id} className="relative bg-gray-900 rounded-lg overflow-hidden flex flex-col group border border-gray-700 hover:border-blue-500 transition-all">
                                <div className="cursor-pointer relative h-40 bg-black" onClick={() => setActiveMedia({ type: event.fileType, id: event.videoId, filename: event.filename })}>
                                    <div className="absolute top-2 left-2 bg-black/70 px-2 py-1 rounded text-[10px] font-bold text-gray-200 uppercase z-10">{event.fileType}</div>
                                    {event.fileType === 'video' ? (
                                        <div className="h-full flex items-center justify-center"><Video size={40} className="text-blue-500" /></div>
                                    ) : (
                                        <img src={`/api/video/${event.videoId}?token=${token}`} className="h-full w-full object-cover" alt="Evidence Source" />
                                    )}
                                </div>
                                <div className="p-3 bg-gray-900 flex flex-col gap-2">
                                    <div className="text-[10px] text-gray-500 truncate">{event.filename}</div>
                                    <div className="flex justify-between items-center">
                                        <select
                                            value={event.status || 'new'}
                                            onChange={(e) => handleStatusChange(event._id, e.target.value)}
                                            className="p-1 text-[10px] font-bold rounded bg-gray-800 text-gray-300 border border-gray-700"
                                        >
                                            <option value="new">New</option>
                                            <option value="reviewed">Reviewed</option>
                                            <option value="archived">Archived</option>
                                        </select>
                                        <button onClick={() => handleDelete(event._id)} className="text-gray-500 hover:text-red-500 transition"><Trash2 size={16} /></button>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            ))}

            {activeMedia && (
                <div className="fixed inset-0 bg-black/95 z-50 flex flex-col items-center justify-center p-4">
                    <div className="w-full max-w-5xl flex justify-between items-center mb-4">
                        <h3 className="text-lg font-bold text-white truncate">{activeMedia.filename}</h3>
                        <button onClick={() => setActiveMedia(null)} className="text-gray-400 hover:text-white p-2 bg-gray-800 rounded-full"><CloseIcon size={24} /></button>
                    </div>
                    <div className="w-full max-w-5xl max-h-[80vh] flex justify-center bg-black rounded-lg overflow-hidden border border-gray-800">
                        {activeMedia.type === 'video' ? (
                            <video controls autoPlay className="max-w-full max-h-[80vh]"><source src={`/api/video/${activeMedia.id}?token=${token}`} type="video/mp4" /></video>
                        ) : (
                            <img src={`/api/video/${activeMedia.id}?token=${token}`} className="max-w-full max-h-[80vh] object-contain" alt="Evidence Expanded View" />
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}