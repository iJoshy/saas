"use client"

import Link from 'next/link';
import { SignInButton, SignedIn, SignedOut, UserButton } from '@clerk/nextjs';

const features = [
  {
    title: 'Clinical-Grade Summaries',
    body: 'Transform raw consultation notes into structured summaries tailored for professional records.',
  },
  {
    title: 'Clear Next Steps',
    body: 'Get concise action items and follow-up plans so every consultation closes with confidence.',
  },
  {
    title: 'Patient-Ready Emails',
    body: 'Generate empathetic, easy-to-read patient communication and send it in one seamless flow.',
  },
];

export default function Home() {
  return (
    <main className="app-canvas min-h-screen">
      <div className="floating-orb floating-orb-a" aria-hidden="true" />
      <div className="floating-orb floating-orb-b" aria-hidden="true" />

      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-5 pb-12 pt-8 md:px-8">
        <nav className="fade-in-up flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-cyan-500 via-blue-500 to-indigo-500 shadow-lg shadow-blue-500/25" />
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">MediNotes</p>
              <p className="text-lg font-semibold text-slate-900">Consultation Studio</p>
            </div>
          </div>

          <div>
            <SignedOut>
              <SignInButton mode="modal">
                <button className="btn-secondary px-5 py-2.5 text-sm">Sign In</button>
              </SignInButton>
            </SignedOut>
            <SignedIn>
              <div className="flex items-center gap-3">
                <Link href="/product" className="btn-primary px-5 py-2.5 text-sm">
                  Open Workspace
                </Link>
                <UserButton showName={true} />
              </div>
            </SignedIn>
          </div>
        </nav>

        <section className="fade-in-up mt-14 grid items-end gap-10 md:mt-20 md:grid-cols-[1.2fr_0.8fr]">
          <div>
            <p className="mb-4 inline-flex rounded-full border border-slate-200 bg-white/80 px-4 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-600 shadow-sm backdrop-blur">
              Built for modern clinics
            </p>
            <h1 className="max-w-3xl text-balance text-4xl font-semibold leading-tight text-slate-900 md:text-6xl">
              Turn consultation notes into polished care documentation in minutes.
            </h1>
            <p className="mt-6 max-w-2xl text-pretty text-lg leading-relaxed text-slate-600 md:text-xl">
              A focused AI workflow for healthcare professionals to produce clean summaries,
              actionable next steps, and patient-friendly communication with zero clutter.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <SignedOut>
                <SignInButton mode="modal">
                  <button className="btn-primary px-6 py-3 text-sm">Start Free Trial</button>
                </SignInButton>
              </SignedOut>
              <SignedIn>
                <Link href="/product" className="btn-primary px-6 py-3 text-sm">
                  Continue to App
                </Link>
              </SignedIn>
              <p className="text-sm text-slate-500">• Compliance-first mindset • Secure-by-default</p>
            </div>
          </div>

          <div className="glass-panel fade-in-up p-6 md:p-7">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Why teams choose MediNotes</p>
            <ul className="mt-5 space-y-4">
              <li className="rounded-xl border border-slate-200/80 bg-white/80 p-4">
                <p className="text-sm font-semibold text-slate-900">Faster chart completion</p>
                <p className="mt-1 text-sm text-slate-600">Reduce admin overhead and reclaim clinic hours each week.</p>
              </li>
              <li className="rounded-xl border border-slate-200/80 bg-white/80 p-4">
                <p className="text-sm font-semibold text-slate-900">Consistent communication</p>
                <p className="mt-1 text-sm text-slate-600">Maintain a professional tone across every patient touchpoint.</p>
              </li>
              <li className="rounded-xl border border-slate-200/80 bg-white/80 p-4">
                <p className="text-sm font-semibold text-slate-900">Auditable workflow</p>
                <p className="mt-1 text-sm text-slate-600">Track visits, history, and outputs in one controlled workspace.</p>
              </li>
            </ul>
          </div>
        </section>

        <section className="fade-in-up mt-14 grid gap-4 md:mt-16 md:grid-cols-3">
          {features.map((feature) => (
            <article key={feature.title} className="glass-panel subtle-rise p-6">
              <h2 className="text-lg font-semibold text-slate-900">{feature.title}</h2>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{feature.body}</p>
            </article>
          ))}
        </section>
      </div>
    </main>
  );
}
