"use client"

import { useState, FormEvent, useCallback, useEffect } from 'react';
import { useAuth } from '@clerk/nextjs';
import DatePicker from 'react-datepicker';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import { Protect, PricingTable, UserButton } from '@clerk/nextjs';

type HistoryListItem = {
    id: number;
    patient_name: string;
    patient_email: string;
    date_of_visit: string;
    created_at: string;
    pinned: number;
};

type HistoryDetail = HistoryListItem & {
    notes: string;
    summary_markdown: string;
};

function formatTimestamp(value: string): string {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString();
}

async function fetchWithFallback(paths: string[], init?: RequestInit): Promise<Response> {
    let lastResponse: Response | null = null;
    for (const path of paths) {
        const res = await fetch(path, init);
        lastResponse = res;
        if (res.ok) return res;
        // Try alternate route only for likely routing mismatches.
        if (res.status !== 404 && res.status !== 405) return res;
    }
    if (lastResponse) return lastResponse;
    throw new Error('No response from history endpoint');
}

function ConsultationWorkspace() {
    const { getToken } = useAuth();

    const [patientName, setPatientName] = useState('');
    const [visitDate, setVisitDate] = useState<Date | null>(new Date());
    const [patientEmail, setPatientEmail] = useState('');
    const [notes, setNotes] = useState('');

    const [output, setOutput] = useState('');
    const [loading, setLoading] = useState(false);

    const [historyItems, setHistoryItems] = useState<HistoryListItem[]>([]);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [historyError, setHistoryError] = useState('');
    const [activeHistoryId, setActiveHistoryId] = useState<number | null>(null);
    const [drawerOpen, setDrawerOpen] = useState(false);
    const [historyQuery, setHistoryQuery] = useState('');
    const [showPinnedOnly, setShowPinnedOnly] = useState(false);

    const loadHistory = useCallback(async () => {
        setHistoryLoading(true);
        setHistoryError('');

        try {
            const jwt = await getToken();
            if (!jwt) {
                setHistoryError('Authentication required');
                return;
            }

            const res = await fetchWithFallback(['/api?action=history', '/api/history', '/history'], {
                headers: {
                    Authorization: `Bearer ${jwt}`,
                },
            });

            if (!res.ok) {
                const errorBody = await res.text();
                throw new Error(`History request failed with status ${res.status}: ${errorBody}`);
            }

            const data = (await res.json()) as { items?: HistoryListItem[] };
            setHistoryItems(data.items ?? []);
        } catch (error) {
            console.error('History load error:', error);
            setHistoryError('Unable to load history');
        } finally {
            setHistoryLoading(false);
        }
    }, [getToken]);

    const loadHistoryDetail = useCallback(
        async (historyId: number) => {
            try {
                const jwt = await getToken();
                if (!jwt) {
                    setOutput('Authentication required');
                    return;
                }

                const res = await fetchWithFallback(
                    [`/api?action=detail&history_id=${historyId}`, `/api/history/${historyId}`, `/history/${historyId}`],
                    {
                    headers: {
                        Authorization: `Bearer ${jwt}`,
                    },
                });

                if (!res.ok) {
                    const errorBody = await res.text();
                    throw new Error(`History detail request failed with status ${res.status}: ${errorBody}`);
                }

                const item = (await res.json()) as HistoryDetail;

                setPatientName(item.patient_name);
                setPatientEmail(item.patient_email);
                setNotes(item.notes);
                setOutput(item.summary_markdown ?? '');
                setActiveHistoryId(item.id);

                const parsedDate = new Date(`${item.date_of_visit}T00:00:00`);
                setVisitDate(Number.isNaN(parsedDate.getTime()) ? null : parsedDate);
                setDrawerOpen(false);
            } catch (error) {
                console.error('History detail error:', error);
                setOutput('Unable to load selected history item.');
            }
        },
        [getToken],
    );

    useEffect(() => {
        void loadHistory();
    }, [loadHistory]);

    async function togglePin(item: HistoryListItem) {
        try {
            const jwt = await getToken();
            if (!jwt) {
                setHistoryError('Authentication required');
                return;
            }

            const res = await fetchWithFallback(
                [`/api?action=pin&history_id=${item.id}`, `/api/history/${item.id}/pin`, `/history/${item.id}/pin`],
                {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${jwt}`,
                },
                body: JSON.stringify({ pinned: item.pinned !== 1 }),
            });

            if (!res.ok) {
                const errorBody = await res.text();
                throw new Error(`Pin update failed with status ${res.status}: ${errorBody}`);
            }

            await loadHistory();
        } catch (error) {
            console.error('Pin update error:', error);
            setHistoryError('Unable to update favorite');
        }
    }

    function resetForm() {
        setPatientName('');
        setVisitDate(new Date());
        setPatientEmail('');
        setNotes('');
        setOutput('');
        setActiveHistoryId(null);
        setDrawerOpen(false);
    }

    async function handleSubmit(e: FormEvent) {
        e.preventDefault();
        setOutput('');
        setLoading(true);
        setActiveHistoryId(null);

        const jwt = await getToken();
        if (!jwt) {
            setOutput('Authentication required');
            setLoading(false);
            return;
        }

        const controller = new AbortController();
        let buffer = '';

        await fetchEventSource('/api', {
            signal: controller.signal,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${jwt}`,
            },
            body: JSON.stringify({
                patient_name: patientName,
                date_of_visit: visitDate?.toISOString().slice(0, 10),
                patient_email: patientEmail,
                notes,
            }),
            onmessage(ev) {
                buffer += `${ev.data}\n`;
                setOutput(buffer);
            },
            onclose() {
                setLoading(false);
                void loadHistory();
            },
            onerror(err) {
                console.error('SSE error:', err);
                controller.abort();
                setLoading(false);
            },
        });
    }

    const filteredHistoryItems = historyItems.filter((item) => {
        if (showPinnedOnly && item.pinned !== 1) return false;
        if (!historyQuery.trim()) return true;

        const q = historyQuery.toLowerCase();
        return (
            item.patient_name.toLowerCase().includes(q) ||
            item.patient_email.toLowerCase().includes(q) ||
            item.date_of_visit.toLowerCase().includes(q)
        );
    });

    return (
        <div className="app-canvas relative min-h-screen md:flex">
            <div className="floating-orb floating-orb-a" aria-hidden="true" />
            <div className="floating-orb floating-orb-c" aria-hidden="true" />

            <aside
                className={`
                    fixed inset-y-0 left-0 z-40 w-80 border-r border-slate-200/70 bg-white/90 backdrop-blur-xl
                    transition-transform duration-300 md:static md:translate-x-0
                    ${drawerOpen ? 'translate-x-0' : '-translate-x-full'}
                `}
            >
                <div className="flex h-full flex-col">
                    <div className="border-b border-slate-200/70 p-4">
                        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-slate-500">Workspace</p>
                        <h2 className="mt-1 text-lg font-semibold text-slate-900">Consultation History</h2>
                    </div>

                    <div className="p-3">
                        <button
                            type="button"
                            onClick={resetForm}
                            className="btn-secondary w-full px-3 py-2.5 text-left text-sm"
                        >
                            + New consultation
                        </button>
                    </div>

                    <div className="space-y-3 border-b border-slate-200/70 px-3 pb-3">
                        <input
                            type="search"
                            value={historyQuery}
                            onChange={(e) => setHistoryQuery(e.target.value)}
                            placeholder="Search patient, email, or date"
                            className="input-polished w-full"
                        />
                        <div className="grid grid-cols-2 gap-2">
                            <button
                                type="button"
                                onClick={() => setShowPinnedOnly(false)}
                                className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                                    !showPinnedOnly
                                        ? 'bg-slate-900 text-white'
                                        : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
                                }`}
                            >
                                All
                            </button>
                            <button
                                type="button"
                                onClick={() => setShowPinnedOnly(true)}
                                className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                                    showPinnedOnly
                                        ? 'bg-slate-900 text-white'
                                        : 'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50'
                                }`}
                            >
                                Favorites
                            </button>
                        </div>
                    </div>

                    <div className="flex-1 space-y-2 overflow-y-auto px-3 pb-4 pt-3">
                        {historyLoading && <p className="px-2 py-2 text-sm text-slate-500">Loading history...</p>}
                        {historyError && <p className="px-2 py-2 text-sm text-rose-600">{historyError}</p>}
                        {!historyLoading && !historyError && filteredHistoryItems.length === 0 && (
                            <p className="px-2 py-2 text-sm text-slate-500">No consultations found.</p>
                        )}

                        {filteredHistoryItems.map((item) => {
                            const isActive = activeHistoryId === item.id;
                            return (
                                <article
                                    key={item.id}
                                    className={`history-card subtle-rise ${isActive ? 'history-card-active' : ''}`}
                                >
                                    <div className="mb-2 flex items-start justify-between gap-2">
                                        <button
                                            type="button"
                                            onClick={() => void loadHistoryDetail(item.id)}
                                            className="min-w-0 flex-1 text-left"
                                        >
                                            <p className="truncate text-sm font-semibold text-slate-900">{item.patient_name}</p>
                                            <p className="truncate text-xs text-slate-500">{item.patient_email}</p>
                                        </button>
                                        <button
                                            type="button"
                                            aria-label={item.pinned === 1 ? 'Unfavorite consultation' : 'Favorite consultation'}
                                            title={item.pinned === 1 ? 'Unfavorite' : 'Favorite'}
                                            onClick={() => void togglePin(item)}
                                            className={`rounded-md px-2 py-1 text-sm transition ${
                                                item.pinned === 1
                                                    ? 'bg-amber-100 text-amber-700 hover:bg-amber-200'
                                                    : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                                            }`}
                                        >
                                            {item.pinned === 1 ? '★' : '☆'}
                                        </button>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => void loadHistoryDetail(item.id)}
                                        className="w-full text-left"
                                    >
                                        <p className="text-xs text-slate-500">Visit: {item.date_of_visit}</p>
                                        <p className="text-xs text-slate-400">Saved: {formatTimestamp(item.created_at)}</p>
                                    </button>
                                </article>
                            );
                        })}
                    </div>
                </div>
            </aside>

            {drawerOpen && (
                <button
                    type="button"
                    aria-label="Close history drawer"
                    className="fixed inset-0 z-30 bg-slate-900/40 md:hidden"
                    onClick={() => setDrawerOpen(false)}
                />
            )}

            <section className="relative z-10 flex-1 px-4 py-6 md:px-8 md:py-8">
                <div className="fade-in-up mx-auto w-full max-w-5xl">
                    <header className="mb-6 flex items-center justify-between rounded-2xl border border-slate-200/70 bg-white/75 px-4 py-3 shadow-sm backdrop-blur md:px-6">
                        <div className="flex items-center gap-3">
                            <button
                                type="button"
                                onClick={() => setDrawerOpen(true)}
                                className="btn-secondary px-3 py-2 text-sm md:hidden"
                            >
                                History
                            </button>
                            <div>
                                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">MediNotes</p>
                                <h1 className="text-xl font-semibold text-slate-900 md:text-2xl">Consultation Studio</h1>
                            </div>
                        </div>
                        <UserButton showName={true} />
                    </header>

                    <form onSubmit={handleSubmit} className="glass-panel fade-in-up space-y-6 p-6 md:p-8">
                        <div className="grid gap-5 md:grid-cols-2">
                            <div className="space-y-2 md:col-span-2">
                                <label htmlFor="patient" className="label-polished">Patient Name</label>
                                <input
                                    id="patient"
                                    type="text"
                                    required
                                    value={patientName}
                                    onChange={(e) => setPatientName(e.target.value)}
                                    className="input-polished w-full"
                                    placeholder="Enter patient's full name"
                                />
                            </div>

                            <div className="space-y-2">
                                <label htmlFor="date" className="label-polished">Date of Visit</label>
                                <DatePicker
                                    id="date"
                                    selected={visitDate}
                                    onChange={(d: Date | null) => setVisitDate(d)}
                                    dateFormat="yyyy-MM-dd"
                                    placeholderText="Select date"
                                    required
                                    className="input-polished w-full"
                                />
                            </div>

                            <div className="space-y-2">
                                <label htmlFor="patient-email" className="label-polished">Patient Email</label>
                                <input
                                    id="patient-email"
                                    type="email"
                                    required
                                    value={patientEmail}
                                    onChange={(e) => setPatientEmail(e.target.value)}
                                    autoComplete="email"
                                    className="input-polished w-full"
                                    placeholder="name@example.com"
                                />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <label htmlFor="notes" className="label-polished">Consultation Notes</label>
                            <textarea
                                id="notes"
                                required
                                rows={8}
                                value={notes}
                                onChange={(e) => setNotes(e.target.value)}
                                className="input-polished w-full resize-y"
                                placeholder="Enter detailed consultation notes..."
                            />
                        </div>

                        <button
                            type="submit"
                            disabled={loading}
                            className="btn-primary w-full px-6 py-3.5 text-sm font-semibold"
                        >
                            {loading ? 'Generating Summary...' : 'Generate Summary'}
                        </button>
                    </form>

                    {output && (
                        <section className="report-shell fade-in-up mt-8 rounded-3xl p-6 md:p-10">
                            <header className="mb-7 border-b border-slate-200/80 pb-4">
                                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Output</p>
                                <h2 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 md:text-3xl">
                                    Consultation Report
                                </h2>
                                <p className="mt-1 text-sm text-slate-500">
                                    Structured summary for clinical records, action planning, and patient communication.
                                </p>
                            </header>
                            <div className="markdown-content max-w-none">
                                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{output}</ReactMarkdown>
                            </div>
                        </section>
                    )}
                </div>
            </section>
        </div>
    );
}

export default function Product() {
    return (
        <main className="app-canvas min-h-screen">
            <Protect
                plan="premium_subscription"
                fallback={
                    <div className="mx-auto max-w-4xl px-4 py-12">
                        <header className="glass-panel fade-in-up mb-8 p-8 text-center">
                            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-slate-500">MediNotes Subscription</p>
                            <h1 className="mt-3 text-4xl font-semibold text-slate-900">Healthcare Professional Plan</h1>
                            <p className="mx-auto mt-3 max-w-2xl text-slate-600">
                                Unlock full consultation workflow, patient-ready summaries, and secure history.
                            </p>
                        </header>
                        <div className="glass-panel p-4 md:p-6">
                            <PricingTable />
                        </div>
                    </div>
                }
            >
                <ConsultationWorkspace />
            </Protect>
        </main>
    );
}
