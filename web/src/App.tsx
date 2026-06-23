import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

interface Clip {
  id: number;
  text: string;
  voice: string;
  url: string;
}

interface VoiceMeta {
  id: string;
  name: string;
  description?: string;
  engine: string;
  enabled?: boolean;
  builtin?: boolean;
  ref_clip?: string | null;
  gender?: "male" | "female" | "unspecified" | null;
  internal_only?: boolean;
}

interface ListenItem {
  file: string;
  caption: string;
  voice?: string;
  favorite?: boolean;
}

interface Variant {
  id: string;
  label: string;
}

interface BakeJob {
  id: string;
  name: string;
  stage: string;
  progress: number;
  status: "running" | "done" | "error";
  error?: string | null;
  voice_id?: string | null;
  n_slices?: number;
  n_samples?: number;
}

type Tab = "synth" | "voicelab" | "library" | "listen" | "tools";
// Voice Lab holds the three ways to MAKE a voice, picked by this sub-mode.
type LabMode = "ttv" | "quick" | "deep";

// Voice "type" is derived from the engine — display-only, never stored.
function voiceTypeLabel(v: VoiceMeta): string {
  if (v.engine === "voxcpm-lora") return "30-Minute Clone";
  if (v.engine === "voxcpm") return "Text to Voice";
  if (v.engine === "chatterbox") return v.ref_clip ? "10-Second Clone" : "Built-in";
  return v.engine;
}
function voiceTypeKey(v: VoiceMeta): "voxcpm" | "clone10" | "clone30" | "builtin" {
  if (v.engine === "voxcpm-lora") return "clone30";
  if (v.engine === "voxcpm") return "voxcpm";
  if (v.engine === "chatterbox" && v.ref_clip) return "clone10";
  return "builtin";
}

// Open-folder glyph for the Clip Library (reveals the file in the OS file manager).
function FolderIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width={18}
      height={18}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

// Favorite glyph for Clip Library clips — filled when starred, outline when not.
function StarIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={18}
      height={18}
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 3.5l2.6 5.3 5.9.85-4.25 4.15 1 5.85L12 16.9l-5.25 2.8 1-5.85L3.5 9.65l5.9-.85z" />
    </svg>
  );
}

// Show/hide control glyph — open eye = shown in Synthesis, slashed eye = hidden.
function EyeIcon({ off }: { off?: boolean }) {
  const common = {
    viewBox: "0 0 24 24",
    width: 20,
    height: 20,
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  return off ? (
    <svg {...common}>
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  ) : (
    <svg {...common}>
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

// Progressive disclosure — plain copy by default, the technical "how" one click away (rules.md §8).
function HowItWorks({ children }: { children: ReactNode }) {
  return (
    <details className="how-it-works">
      <summary>How does this work?</summary>
      <div className="how-it-works-body">{children}</div>
    </details>
  );
}

// The bake pipeline's ordered stages, mirrored from server/app/voicelab.py STAGES.
const BAKE_STAGES: { key: string; label: string }[] = [
  { key: "converting", label: "Convert" },
  { key: "slicing", label: "Slice" },
  { key: "transcribing", label: "Transcribe" },
  { key: "training", label: "Train" },
  { key: "registering", label: "Register" },
  { key: "ready", label: "Ready" },
];

const SAMPLE = "A gentle rain fell over the city as the evening lights came on.";

// Selectable UI themes. Extensible: add an {id,label} here + a matching .theme-<id>
// block in styles/themes.css. OpenVoice (clean) is the default.
const THEMES = [
  { id: "openvoice", label: "OpenVoice" },
  { id: "honededge", label: "HonedEdge" },
] as const;
type ThemeId = (typeof THEMES)[number]["id"];

// Light / dark / system. Applied as a `mode-<resolved>` class on the root shell
// ("system" resolves via prefers-color-scheme). Orthogonal to the theme.
const MODES = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
] as const;
type Mode = (typeof MODES)[number]["id"];

export default function App() {
  const [tab, setTab] = useState<Tab>(() => {
    const saved = localStorage.getItem("tl_tab");
    // "design" folded into Voice Lab; "library" merged into Voice Synthesis — migrate.
    if (saved === "design") return "voicelab";
    if (saved === "capture") return "voicelab"; // Capture folded into Voice Lab's modes
    return (saved as Tab) || "synth";
  });
  const [labMode, setLabMode] = useState<LabMode>("ttv");

  // UI theme — persisted; default OpenVoice (the clean look). Applied as a class on
  // the root shell so themes.css can reskin everything.
  const [theme, setTheme] = useState<ThemeId>(() => {
    const saved = localStorage.getItem("ov_theme");
    return THEMES.some((t) => t.id === saved) ? (saved as ThemeId) : "openvoice";
  });
  useEffect(() => {
    localStorage.setItem("ov_theme", theme);
  }, [theme]);

  // Light/dark/system mode — persisted; "system" follows the OS preference live.
  const [mode, setMode] = useState<Mode>(() => {
    const saved = localStorage.getItem("ov_mode");
    return MODES.some((m) => m.id === saved) ? (saved as Mode) : "system";
  });
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true
  );
  useEffect(() => {
    localStorage.setItem("ov_mode", mode);
  }, [mode]);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  const resolvedMode = mode === "system" ? (systemDark ? "dark" : "light") : mode;

  const [text, setText] = useState("Hello from OpenVoice.");
  const [exaggeration, setExaggeration] = useState(0.5);
  const [cfg, setCfg] = useState(0.5);
  const [rate, setRate] = useState(1.0); // pitch-preserving speed (atempo)
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("Standing by.");
  const [clips, setClips] = useState<Clip[]>([]);
  const [voices, setVoices] = useState<VoiceMeta[]>([]);
  const [voiceId, setVoiceId] = useState("default");

  // Voice design (A/B/C audition flow)
  const [designName, setDesignName] = useState("");
  const [designDesc, setDesignDesc] = useState("");
  const [designText, setDesignText] = useState(SAMPLE);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [designing, setDesigning] = useState(false);
  const [designStatus, setDesignStatus] = useState("Describe a voice, generate three takes, keep the best.");

  const [listenItems, setListenItems] = useState<ListenItem[]>([]);
  // Clip Library filters.
  const [clipVoice, setClipVoice] = useState("all");
  const [clipFavOnly, setClipFavOnly] = useState(false);

  // Voice Lab — bake a usable voice from an uploaded file (no command line).
  const [labName, setLabName] = useState("");
  const [labFile, setLabFile] = useState<File | null>(null);
  const [labJob, setLabJob] = useState<BakeJob | null>(null);
  const [labBusy, setLabBusy] = useState(false);
  const [labStatus, setLabStatus] = useState(
    "Drop a clean audio file (a 30-min narration works best), name it, and Bake."
  );
  const labPoll = useRef<number | null>(null);
  const labInput = useRef<HTMLInputElement | null>(null);

  // Voice Lab · 10-Second Clone — instant Chatterbox zero-shot from a short sample.
  const [quickName, setQuickName] = useState("");
  const [quickFile, setQuickFile] = useState<File | null>(null);
  const [quickBusy, setQuickBusy] = useState(false);
  const [quickStatus, setQuickStatus] = useState(
    "Drop a short, clean sample (~10–30s), name it, and Clone."
  );
  const quickInput = useRef<HTMLInputElement | null>(null);

  // Required curation metadata for any NEW voice made in Voice Lab (shared across the
  // three modes — you fill one at a time). Gender must be chosen before creating.
  const [newGender, setNewGender] = useState<"" | "male" | "female" | "unspecified">("");
  const [newInternal, setNewInternal] = useState(false);

  // Voice Library filters (narrow the list — independent of show/hide visibility).
  const [libType, setLibType] = useState<"all" | "voxcpm" | "clone10" | "clone30">("all");
  const [libGender, setLibGender] = useState<"all" | "male" | "female" | "unspecified">("all");
  const [libVis, setLibVis] = useState<"all" | "shown" | "hidden">("all");
  const [libInternal, setLibInternal] = useState<"all" | "internal" | "public">("all");

  // Warn before generating with an internal-use-only voice (replaces the visual flag).
  const [internalWarn, setInternalWarn] = useState(false);

  // Voice capture (record a browser tab's audio — e.g. an Audible sample)
  const mediaRec = useRef<MediaRecorder | null>(null);
  const wakeLock = useRef<any>(null); // screen wake lock held during a capture (keeps the PC awake)
  const capChunks = useRef<Blob[]>([]);
  const capStream = useRef<MediaStream | null>(null);
  const [recording, setRecording] = useState(false);
  // Clone-mode input source: a file upload, or a live tab capture (B). Plus whether
  // to also drop the raw source .weba into Downloads (default on for the long 30-min
  // unattended bake — it's the recovery file if the bake hiccups — off for quick).
  const [quickSource, setQuickSource] = useState<"upload" | "capture">("upload");
  const [deepSource, setDeepSource] = useState<"upload" | "capture">("upload");
  const [toolStatus, setToolStatus] = useState("");
  const [toolsTab, setToolsTab] = useState<"capture" | "options">("capture");
  const [deepSaveSrc, setDeepSaveSrc] = useState(true);
  // 30-minute timed capture: kick off at night, walk away, auto-saves to Downloads.
  const capTimer = useRef<number | null>(null);
  const capTick = useRef<number | null>(null);
  const [capRemaining, setCapRemaining] = useState<number | null>(null);

  const idRef = useRef(0);

  async function loadVoices() {
    const v: VoiceMeta[] = await fetch("/api/voices").then((r) => r.json());
    setVoices(v);
    return v;
  }

  // Pre-generate a new voice's Library preview so there's no first-play delay.
  function warmSample(id: string) {
    fetch(`/api/voices/${id}/sample`).catch(() => {});
  }

  async function loadListen() {
    const r = await fetch("/api/listen").then((r) => r.json());
    setListenItems(r.items || []);
  }

  async function deleteClip(file: string) {
    if (!window.confirm(`Delete clip “${file}”?\nThis removes the file from disk.`)) return;
    await fetch(`/api/listen/${encodeURIComponent(file)}`, { method: "DELETE" });
    await loadListen();
  }

  async function revealClip(file: string) {
    await fetch(`/api/listen/${encodeURIComponent(file)}/reveal`, { method: "POST" }).catch(
      () => {}
    );
  }

  async function toggleFavorite(file: string, favorite: boolean) {
    // Optimistic flip, then persist; reload on failure to resync.
    setListenItems((items) =>
      items.map((it) => (it.file === file ? { ...it, favorite } : it))
    );
    try {
      await fetch(`/api/listen/${encodeURIComponent(file)}/favorite`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ favorite }),
      });
    } catch {
      loadListen().catch(() => {});
    }
  }

  async function toggleEnabled(id: string, enabled: boolean) {
    await fetch(`/api/voices/${id}/enabled`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    // Can't speak as a disabled voice — drop the selection back to default.
    if (!enabled && voiceId === id) setVoiceId("default");
    await loadVoices();
  }

  // Library → Text-to-Voice cookbook: drop a saved voice's creation prompt back into
  // the designer so you can riff on the exact recipe that made it.
  function reusePrompt(prompt: string) {
    setDesignDesc(prompt);
    setLabMode("ttv");
    setTab("voicelab");
  }

  async function deleteVoice(id: string) {
    await fetch(`/api/voices/${id}`, { method: "DELETE" });
    if (voiceId === id) setVoiceId("default");
    await loadVoices();
  }

  useEffect(() => {
    loadVoices().catch(() => setStatus("Could not reach engine."));
    loadListen().catch(() => {});
  }, []);

  // Remember the active tab across refreshes.
  useEffect(() => {
    localStorage.setItem("tl_tab", tab);
  }, [tab]);

  // Gate: an internal-use-only voice prompts a confirm modal first; otherwise straight through.
  function generate() {
    const v = voices.find((x) => x.id === voiceId);
    if (v?.internal_only) {
      setInternalWarn(true);
      return;
    }
    doGenerate();
  }

  async function doGenerate() {
    const line = text.trim();
    if (!line || busy) return;
    setBusy(true);
    setStatus("Synthesizing…");
    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: line,
          voice_id: voiceId,
          exaggeration,
          cfg_weight: cfg,
          rate, // pitch-preserving speed
          save: true, // also land it in the Clip Library
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const url = URL.createObjectURL(await res.blob());
      const voiceName = voices.find((v) => v.id === voiceId)?.name ?? voiceId;
      // Bump the id OUTSIDE the updater — StrictMode double-invokes updaters in dev, and a
      // side effect in there (++) ran twice, so ids jumped by 2. The updater must stay pure.
      const clipId = ++idRef.current;
      setClips((c) => [{ id: clipId, text: line, voice: voiceName, url }, ...c]);
      setStatus("Ready.");
      loadListen().catch(() => {}); // refresh the Clip Library with the new clip
    } catch (e) {
      setStatus("Error — " + String(e));
    } finally {
      setBusy(false);
    }
  }

  async function generateVariants() {
    const desc = designDesc.trim();
    if (!desc || designing) return;
    setDesigning(true);
    setVariants([]);
    setDesignStatus("Generating three takes…");
    try {
      const res = await fetch("/api/design/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: desc, text: designText.trim() || SAMPLE }),
      });
      if (!res.ok) throw new Error(await res.text());
      const r = await res.json();
      setVariants(r.variants || []);
      setDesignStatus("Audition A / B / C, then save the keeper.");
    } catch (e) {
      setDesignStatus("Error — " + String(e));
    } finally {
      setDesigning(false);
    }
  }

  async function saveVariant(variantId: string) {
    const name = designName.trim();
    const desc = designDesc.trim();
    if (!name) {
      setDesignStatus("Give the voice a name first.");
      return;
    }
    if (!newGender) {
      setDesignStatus("Pick Male or Female first.");
      return;
    }
    try {
      const res = await fetch("/api/design/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          description: desc,
          variant_id: variantId,
          gender: newGender,
          internal_only: false, // a designed Text-to-Voice voice is never a real person
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const voice = await res.json();
      await loadVoices();
      setVoiceId(voice.id);
      warmSample(voice.id);
      setVariants([]);
      setDesignName("");
      setDesignDesc("");
      setNewGender("");
      setNewInternal(false);
      setDesignStatus(`Saved “${voice.name}”. Selected for synthesis.`);
    } catch (e) {
      setDesignStatus("Error — " + String(e));
    }
  }


  // --- Voice Lab: 10-Second Quick Clone ---
  // Name + gender are required for either source (set up-front so a capture can run
  // unattended). File is validated separately (a capture supplies it on stop).
  function quickReady(): boolean {
    if (!quickName.trim()) {
      setQuickStatus("Give the voice a name first.");
      return false;
    }
    if (!newGender) {
      setQuickStatus("Pick Male or Female first.");
      return false;
    }
    return true;
  }

  async function submitQuickClone(file: File) {
    setQuickBusy(true);
    setQuickStatus("Cloning…");
    try {
      const fd = new FormData();
      fd.append("name", quickName.trim());
      fd.append("file", file);
      fd.append("gender", newGender);
      fd.append("internal_only", String(newInternal));
      const res = await fetch("/api/voices/clone", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      const voice = await res.json();
      await loadVoices();
      setVoiceId(voice.id);
      warmSample(voice.id);
      setQuickStatus(`Cloned “${voice.name}” — in the Library and selected for Synthesis.`);
      setQuickName("");
      setQuickFile(null);
      setNewGender("");
      setNewInternal(false);
      if (quickInput.current) quickInput.current.value = "";
    } catch (e) {
      setQuickStatus("Error — " + String(e));
    } finally {
      setQuickBusy(false);
    }
  }

  async function startQuickClone() {
    if (quickBusy) return;
    if (!quickFile) {
      setQuickStatus("Choose a short audio sample first.");
      return;
    }
    if (!quickReady()) return;
    await submitQuickClone(quickFile);
  }

  async function startQuickCapture() {
    if (quickBusy || recording) return;
    if (!quickReady()) return;
    await runCapture({
      durationMs: 15000, // one-shot: record exactly 15s (Chatterbox's clean-clip sweet spot)
      saveToDownloads: true, // always keep the source clip in Downloads
      label: "15 sec",
      setStatus: setQuickStatus,
      onBlob: (file) => submitQuickClone(file),
    });
  }

  // --- Voice Lab: bake job + polling ---
  async function pollBake(id: string) {
    try {
      const j: BakeJob = await fetch(`/api/voicelab/jobs/${id}`).then((r) => r.json());
      setLabJob(j);
      if (j.status !== "running") {
        if (labPoll.current !== null) {
          clearInterval(labPoll.current);
          labPoll.current = null;
        }
        setLabBusy(false);
        if (j.status === "done" && j.voice_id) {
          await loadVoices();
          setVoiceId(j.voice_id);
          warmSample(j.voice_id);
          setLabStatus(`Done — “${j.name}” is in the Library and selected for Synthesis.`);
          setLabName("");
          setLabFile(null);
          setNewGender("");
          setNewInternal(false);
          if (labInput.current) labInput.current.value = "";
        } else if (j.status === "error") {
          setLabStatus("Bake failed — " + (j.error || "unknown error"));
        }
      }
    } catch {
      // transient network blip — keep polling on the next tick
    }
  }

  function deepReady(): boolean {
    if (!labName.trim()) {
      setLabStatus("Give the voice a name first.");
      return false;
    }
    if (!newGender) {
      setLabStatus("Pick Male or Female first.");
      return false;
    }
    return true;
  }

  async function submitDeepBake(file: File) {
    setLabBusy(true);
    setLabJob(null);
    setLabStatus("Uploading…");
    try {
      const fd = new FormData();
      fd.append("name", labName.trim());
      fd.append("file", file);
      fd.append("gender", newGender);
      fd.append("internal_only", String(newInternal));
      const res = await fetch("/api/voicelab/bake", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      const { job_id } = await res.json();
      setLabStatus("Baking — convert → slice → transcribe → train → register. Training takes longest.");
      labPoll.current = window.setInterval(() => pollBake(job_id), 2000);
      pollBake(job_id);
    } catch (e) {
      setLabStatus("Error — " + String(e));
      setLabBusy(false);
    }
  }

  async function startBake() {
    if (labBusy) return;
    if (!labFile) {
      setLabStatus("Choose an audio file first.");
      return;
    }
    if (!deepReady()) return;
    await submitDeepBake(labFile);
  }

  // The unattended magic: record a tab for 30 minutes, walk away, and it bakes itself
  // the moment the capture stops. Name + gender are required before we start.
  async function startDeepCapture() {
    if (labBusy || recording) return;
    if (!deepReady()) return;
    await runCapture({
      durationMs: 30 * 60 * 1000,
      saveToDownloads: deepSaveSrc,
      label: "30 min",
      setStatus: setLabStatus,
      onBlob: (file) => submitDeepBake(file),
    });
  }

  // Stop polling if the component unmounts mid-bake (the job keeps running server-side).
  useEffect(
    () => () => {
      if (labPoll.current !== null) clearInterval(labPoll.current);
    },
    []
  );

  function stopCaptureTracks() {
    capStream.current?.getTracks().forEach((t) => t.stop());
    capStream.current = null;
  }

  function stopCapture() {
    if (mediaRec.current && mediaRec.current.state !== "inactive") mediaRec.current.stop();
  }

  function clearCapTimers() {
    if (capTimer.current !== null) {
      clearTimeout(capTimer.current);
      capTimer.current = null;
    }
    if (capTick.current !== null) {
      clearInterval(capTick.current);
      capTick.current = null;
    }
    setCapRemaining(null);
  }

  // One capture path feeding the clone modes (B). durationMs=null → manual stop
  // (Quick); a number → timed auto-stop (Deep, unattended). On stop it optionally
  // drops the raw source .weba into Downloads, then hands the captured File to
  // onBlob (which clones or bakes it). AGC/echo/noise off → a flat, faithful copy.
  async function runCapture(opts: {
    durationMs: number | null;
    saveToDownloads: boolean;
    label: string;
    setStatus: (s: string) => void;
    onBlob: (file: File) => void | Promise<void>;
  }) {
    if (recording) return;
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: { autoGainControl: false, echoCancellation: false, noiseSuppression: false },
      });
      const audio = stream.getAudioTracks()[0];
      if (!audio) {
        stream.getTracks().forEach((t) => t.stop());
        opts.setStatus('No audio track — re-share and check "Also share tab audio".');
        return;
      }
      capStream.current = stream;
      const rec = new MediaRecorder(new MediaStream([audio]), { mimeType: "audio/webm" });
      capChunks.current = [];
      rec.ondataavailable = (e) => {
        if (e.data.size) capChunks.current.push(e.data);
      };
      rec.onstop = async () => {
        clearCapTimers();
        const blob = new Blob(capChunks.current, { type: "audio/webm" });
        const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
        const fname = `openvoice-capture-${ts}.weba`;
        if (opts.saveToDownloads) {
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = fname; // audio-only WebM → .weba (pipeline convention)
          document.body.appendChild(a);
          a.click();
          a.remove();
        }
        stopCaptureTracks();
        setRecording(false);
        try { await wakeLock.current?.release(); } catch { /* already gone */ }
        wakeLock.current = null;
        await opts.onBlob(new File([blob], fname, { type: "audio/webm" }));
      };
      // If the share is ended from Chrome's own banner, stop (and submit) cleanly.
      audio.onended = () => {
        if (rec.state !== "inactive") rec.stop();
      };
      rec.start(5000); // timeslice so chunks flush over a long recording
      mediaRec.current = rec;
      setRecording(true);
      // Hold a screen wake lock so the machine doesn't idle-sleep mid-record (which would
      // kill the capture — and, for the 30-min path, the train that follows).
      try {
        wakeLock.current = await (navigator as any).wakeLock?.request("screen");
      } catch {
        /* unsupported or denied — non-fatal */
      }
      if (opts.durationMs != null) {
        capTimer.current = window.setTimeout(() => {
          if (rec.state !== "inactive") rec.stop();
        }, opts.durationMs);
        let remaining = Math.round(opts.durationMs / 1000);
        setCapRemaining(remaining);
        capTick.current = window.setInterval(() => {
          remaining -= 1;
          setCapRemaining(remaining > 0 ? remaining : 0);
        }, 1000);
        opts.setStatus(`Recording ${opts.label} — you can walk away; it finishes on its own.`);
      } else {
        opts.setStatus(`Recording ${opts.label} — play the audio, then click Stop.`);
      }
    } catch (e) {
      try { await wakeLock.current?.release(); } catch { /* none held */ }
      wakeLock.current = null;
      opts.setStatus("Cancelled or blocked — " + String(e));
    }
  }

  // Standalone capture utilities (Tools tab): record audio from a tab and save it to
  // Downloads only — no clone, no bake. Same engine (runCapture) as the in-process clones.
  async function toolCapture(durationMs: number | null, label: string) {
    if (recording) return;
    await runCapture({
      durationMs,
      saveToDownloads: true,
      label,
      setStatus: setToolStatus,
      onBlob: () => setToolStatus("Saved to Downloads."),
    });
  }

  function tabBtn(id: Tab, label: string) {
    return (
      <button
        className={tab === id ? "tab-btn active" : "tab-btn"}
        onClick={() => {
          setTab(id);
          if (id === "listen") loadListen().catch(() => {});
        }}
      >
        {label}
      </button>
    );
  }

  // Required curation controls for the Voice Lab creation modes. The internal-only
  // flag is for cloned real people (don't-distribute) — a designed Text-to-Voice
  // voice isn't a real person, so that mode hides it (showInternal=false).
  function creationMetaRow(showInternal: boolean) {
    return (
      <div className="meta-row">
        <div className="meta-gender">
          <span className="meta-label">Gender *</span>
          <select
            className="voice-select"
            value={newGender}
            onChange={(e) => setNewGender(e.target.value as "" | "male" | "female" | "unspecified")}
          >
            <option value="">— select —</option>
            <option value="male">Male</option>
            <option value="female">Female</option>
            <option value="unspecified">Unspecified</option>
          </select>
        </div>
        {showInternal && (
          <label className="meta-internal">
            <input
              type="checkbox"
              checked={newInternal}
              onChange={(e) => setNewInternal(e.target.checked)}
            />
            Internal use only
          </label>
        )}
      </div>
    );
  }


  const libVoices = voices.filter((v) => {
    if (libGender !== "all" && v.gender !== libGender) return false;
    if (libVis === "shown" && v.enabled === false) return false;
    if (libVis === "hidden" && v.enabled !== false) return false;
    if (libInternal === "internal" && !v.internal_only) return false;
    if (libInternal === "public" && v.internal_only) return false;
    if (libType !== "all" && voiceTypeKey(v) !== libType) return false;
    return true;
  });

  return (
    <div className={`ov-shell theme-${theme} mode-${resolvedMode}`}>
      <div className="elbow-tl" />
      <div className="bar-top">
        <div className="brand-lockup">
          <img className="brand-logo" src="/favicon.svg" alt="" />
          <span className="label">OpenVoice</span>
        </div>
        <div className="bar-top-right">
          <span className="tagline">Self-hosted voice studio</span>
        </div>
      </div>

      <div className="rail">
        <div className="block" style={{ background: "var(--ov-tan)" }}>47-A</div>
        <div className="block" style={{ background: "var(--ov-mauve)" }}>21</div>
        <div className="block tall" style={{ background: "var(--ov-violet)" }}>SYNTH</div>
        <div className="block" style={{ background: "var(--ov-plum)" }}>VOX</div>
        <div className="block" style={{ background: "var(--ov-salmon)" }}>LIB</div>
        <div className="block" style={{ background: "var(--ov-sky)" }}>24·12</div>
      </div>

      <div className="content studio">
        <div className="tab-bar">
          {tabBtn("voicelab", "Voice Lab")}
          {tabBtn("synth", "Voice Synthesis")}
          {tabBtn("library", "Voice Library")}
          {tabBtn("listen", "Clip Library")}
          {tabBtn("tools", "Tools")}
        </div>

        <div className="studio-body">
        {tab === "synth" && (
          <div className="tts-form synth-split">
            {/* Left pane: voice list — audition (sample), manage (on/off, delete), and
                pick the active voice. Scrolls independently of the synthesis pane. */}
            <div className="synth-left">
            <div className="voice-list">
              {voices
                .filter((v) => v.enabled !== false)
                .map((v) => {
                  const isActive = v.id === voiceId;
                  return (
                    <div className={"voice-row" + (isActive ? " active" : "")} key={v.id}>
                      <button
                        className="voice-row-pick"
                        onClick={() => setVoiceId(v.id)}
                        title="Use this voice"
                      >
                        <span className="voice-row-name">
                          {isActive ? "▶ " : ""}
                          {v.name}
                        </span>
                        <span className="voice-row-meta">
                          {voiceTypeLabel(v)}
                          {v.gender ? ` · ${v.gender}` : ""}
                        </span>
                      </button>
                      {v.ref_clip || v.builtin ? (
                        <audio
                          className="voice-sample"
                          src={`/api/voices/${v.id}/sample?v=2`}
                          controls
                          preload="none"
                        />
                      ) : (
                        <span className="voice-row-meta">no preview</span>
                      )}
                    </div>
                  );
                })}
            </div>
            </div>

            {/* Right pane: the synthesis workspace + output, also scrolls on its own. */}
            <div className="synth-right">
            <div className="active-voice">
              Speaking as <b>{voices.find((v) => v.id === voiceId)?.name ?? voiceId}</b>
            </div>

            <textarea
              className="tts-text"
              rows={4}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Enter text for the computer to speak…"
            />
            <div className="tts-knobs">
              <label>
                Expressiveness · {exaggeration.toFixed(2)}
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={exaggeration}
                  onChange={(e) => setExaggeration(Number(e.target.value))}
                />
              </label>
              <label>
                Guidance · {cfg.toFixed(2)}
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={cfg}
                  onChange={(e) => setCfg(Number(e.target.value))}
                />
              </label>
              <label>
                Speed · {rate.toFixed(2)}×
                <input
                  type="range"
                  min={0.7}
                  max={1.3}
                  step={0.05}
                  value={rate}
                  onChange={(e) => setRate(Number(e.target.value))}
                />
              </label>
            </div>
            <div className="tts-actions">
              <button className="answer go" disabled={busy} onClick={generate}>
                {busy ? "Working…" : "Generate"}
              </button>
              <span className="tts-status">{status}</span>
            </div>

            <div className="tts-list">
              {clips.map((c) => (
                <div className="tts-clip" key={c.id}>
                  <div className="tts-clip-head">
                    <span className="tts-clip-name">#{c.id}</span>
                    <span className="tts-clip-meta">
                      [{c.voice}] {c.text.slice(0, 64)}
                    </span>
                    <button className="tts-reuse" onClick={() => setText(c.text)}>
                      reuse
                    </button>
                  </div>
                  <audio className="tts-audio" src={c.url} controls autoPlay />
                </div>
              ))}
            </div>
            </div>
          </div>
        )}

        {tab === "library" && (
          <div className="listen-panel">
            <div className="lib-filters">
              <label className="lib-filter">
                Type
                <select className="voice-select" value={libType} onChange={(e) => setLibType(e.target.value as typeof libType)}>
                  <option value="all">All</option>
                  <option value="voxcpm">Text to Voice</option>
                  <option value="clone10">10-Second Clone</option>
                  <option value="clone30">30-Minute Clone</option>
                </select>
              </label>
              <label className="lib-filter">
                Gender
                <select className="voice-select" value={libGender} onChange={(e) => setLibGender(e.target.value as typeof libGender)}>
                  <option value="all">All</option>
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                  <option value="unspecified">Unspecified</option>
                </select>
              </label>
              <label className="lib-filter">
                Visibility
                <select className="voice-select" value={libVis} onChange={(e) => setLibVis(e.target.value as typeof libVis)}>
                  <option value="all">All</option>
                  <option value="shown">Shown</option>
                  <option value="hidden">Hidden</option>
                </select>
              </label>
              <label className="lib-filter">
                Use
                <select className="voice-select" value={libInternal} onChange={(e) => setLibInternal(e.target.value as typeof libInternal)}>
                  <option value="all">All</option>
                  <option value="public">Public</option>
                  <option value="internal">Internal only</option>
                </select>
              </label>
              <span className="lib-count">{libVoices.length} of {voices.length}</span>
            </div>

            {libVoices.map((v) => (
              <div className="voice-row lib-row" key={v.id}>
                <div className="voice-row-main">
                  <span className="voice-row-name">{v.name}</span>
                  <span className="voice-row-meta">{voiceTypeLabel(v)}</span>
                  {v.engine === "voxcpm" && v.description ? (
                    <div className="voice-prompt">
                      <button
                        className="prompt-chip"
                        title={`${v.description}\n\n(click to reuse in Text to Voice)`}
                        onClick={() => reusePrompt(v.description!)}
                      >
                        Prompt
                      </button>
                    </div>
                  ) : null}
                </div>
                <div className="lib-controls">
                  <span className="gender-tag">{v.gender ? v.gender.toUpperCase() : "—"}</span>
                  <button
                    className={"eye-btn" + (v.enabled === false ? " off" : "")}
                    title={
                      v.enabled !== false
                        ? "Shown in Synthesis — click to hide"
                        : "Hidden from Synthesis — click to show"
                    }
                    onClick={() => toggleEnabled(v.id, v.enabled === false)}
                  >
                    <EyeIcon off={v.enabled === false} />
                  </button>
                  {v.ref_clip || v.builtin ? (
                    <audio
                      className="voice-sample lib-sample"
                      src={`/api/voices/${v.id}/sample?v=2`}
                      controls
                      preload="none"
                    />
                  ) : (
                    <span className="voice-row-meta">no preview</span>
                  )}
                  <button
                    className="voice-del"
                    disabled={v.builtin}
                    title={v.builtin ? "The built-in voice can't be deleted" : "Delete this voice"}
                    onClick={() => deleteVoice(v.id)}
                  >
                    delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}


        {tab === "voicelab" && (
          <div className="tts-form">
            <div className="mode-seg">
              <button
                className={labMode === "ttv" ? "mode-btn active" : "mode-btn"}
                onClick={() => setLabMode("ttv")}
              >
                Text to Voice
              </button>
              <button
                className={labMode === "quick" ? "mode-btn active" : "mode-btn"}
                onClick={() => setLabMode("quick")}
              >
                Quick Clone
              </button>
              <button
                className={labMode === "deep" ? "mode-btn active" : "mode-btn"}
                onClick={() => setLabMode("deep")}
              >
                30-Minute Clone
              </button>
            </div>

            {labMode === "ttv" && (
              <>
                <div className="emo-tag-hint">
                  Invent a voice from a description — no recording needed. Name it, describe the
                  voice and a line to hear it say, then generate three takes and keep your favorite.
                  <HowItWorks>
                    There's no sample involved — a voice-design model builds a brand-new voice from
                    your description alone. Each take is a fresh interpretation, so generate a few
                    and keep the one that fits.
                  </HowItWorks>
                </div>
                <div className="design-row">
                  <input
                    className="design-input"
                    placeholder="Voice name (e.g. Old Spacer)"
                    value={designName}
                    onChange={(e) => setDesignName(e.target.value)}
                  />
                  <input
                    className="design-input design-desc"
                    placeholder="Describe the voice — e.g. a warm, gravelly old spacer, unhurried"
                    value={designDesc}
                    onChange={(e) => setDesignDesc(e.target.value)}
                  />
                </div>
                <input
                  className="design-input"
                  style={{ marginTop: "8px" }}
                  placeholder="Sample line to audition with"
                  value={designText}
                  onChange={(e) => setDesignText(e.target.value)}
                />
                {creationMetaRow(false)}
                <div className="tts-actions">
                  <button className="answer go" disabled={designing} onClick={generateVariants}>
                    {designing ? "Generating…" : "Generate 3 Takes"}
                  </button>
                  <span className="tts-status">{designStatus}</span>
                </div>
                {variants.length > 0 && (
                  <div className="variant-grid">
                    {variants.map((v) => (
                      <div className="variant-card" key={v.id}>
                        <div className="variant-label">Take {v.label}</div>
                        <audio className="tts-audio" src={`/api/design/preview/${v.id}`} controls />
                        <button
                          className="answer go variant-save"
                          onClick={() => saveVariant(v.id)}
                        >
                          Save {v.label}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {labMode === "quick" && (
              <>
                <div className="emo-tag-hint">
                  The fastest way to copy a real voice: record 15 seconds, or upload a short,
                  <b>clean</b> clip — it's ready right away. A little less faithful than the
                  30-Minute clone, but no waiting.
                  <HowItWorks>
                    This is zero-shot cloning (the Chatterbox engine): the model listens to your
                    clip and imitates the voice on the spot — no training step, so it's instant. For
                    a more faithful copy, use the 30-Minute clone.
                  </HowItWorks>
                </div>
                <input
                  className="design-input"
                  placeholder="Voice name"
                  value={quickName}
                  disabled={quickBusy}
                  onChange={(e) => setQuickName(e.target.value)}
                />
                {creationMetaRow(true)}
                <div className="source-seg">
                  <button
                    type="button"
                    className={quickSource === "upload" ? "mini-btn active" : "mini-btn"}
                    onClick={() => setQuickSource("upload")}
                  >
                    Upload file
                  </button>
                  <button
                    type="button"
                    className={quickSource === "capture" ? "mini-btn active" : "mini-btn"}
                    onClick={() => setQuickSource("capture")}
                  >
                    Record from tab
                  </button>
                </div>
                {quickSource === "upload" ? (
                  <div className="tts-actions">
                    <input
                      ref={quickInput}
                      className="design-input bake-input"
                      type="file"
                      accept=".weba,.webm,.wav,.mp3,.m4a,.flac,.ogg,audio/*"
                      disabled={quickBusy}
                      onChange={(e) => setQuickFile(e.target.files?.[0] ?? null)}
                    />
                    <button className="answer go" disabled={quickBusy} onClick={startQuickClone}>
                      {quickBusy ? "Cloning…" : "Clone"}
                    </button>
                    <span className="tts-status">{quickStatus}</span>
                  </div>
                ) : (
                  <div className="tts-actions">
                    {!recording ? (
                      <button className="answer go" disabled={quickBusy} onClick={startQuickCapture}>
                        {quickBusy ? "Cloning…" : "Record 15s & Clone"}
                      </button>
                    ) : (
                      <button className="answer idk" onClick={stopCapture}>
                        Stop early
                      </button>
                    )}
                    {recording && capRemaining !== null && (
                      <span className="tts-status">{capRemaining}s left</span>
                    )}
                    <span className="tts-status">{quickStatus}</span>
                  </div>
                )}
              </>
            )}

            {labMode === "deep" && (
              <>
                <div className="emo-tag-hint">
                  The most faithful way to copy a voice: give it about 30 minutes of clear speech
                  and it learns the voice in depth. Upload a file, or <b>record a tab for 30 minutes
                  and walk away</b> — the voice builds itself when the recording ends and lands in
                  your Library. Building takes a few minutes.
                  <HowItWorks>
                    From your recording it trains a small voice model just for this speaker (a
                    VoxCPM2 LoRA adapter): it splits the audio into clips, transcribes them, and
                    fine-tunes the model to match. The <b>Train</b> step is the slow part.
                  </HowItWorks>
                </div>
                <input
                  className="design-input"
                  placeholder="Voice name (e.g. Narrator, Pastor John)"
                  value={labName}
                  disabled={labBusy}
                  onChange={(e) => setLabName(e.target.value)}
                />
                {creationMetaRow(true)}
                <div className="source-seg">
                  <button
                    type="button"
                    className={deepSource === "upload" ? "mini-btn active" : "mini-btn"}
                    onClick={() => setDeepSource("upload")}
                  >
                    Upload file
                  </button>
                  <button
                    type="button"
                    className={deepSource === "capture" ? "mini-btn active" : "mini-btn"}
                    onClick={() => setDeepSource("capture")}
                  >
                    Record 30 min
                  </button>
                </div>
                {deepSource === "upload" ? (
                  <div className="tts-actions">
                    <input
                      ref={labInput}
                      className="design-input bake-input"
                      type="file"
                      accept=".weba,.webm,.wav,.mp3,.m4a,.flac,.ogg,audio/*"
                      disabled={labBusy}
                      onChange={(e) => setLabFile(e.target.files?.[0] ?? null)}
                    />
                    <button className="answer go" disabled={labBusy} onClick={startBake}>
                      {labBusy ? "Baking…" : "Bake"}
                    </button>
                    <span className="tts-status">{labStatus}</span>
                  </div>
                ) : (
                  <div className="tts-actions">
                    {!recording ? (
                      <button className="answer go" disabled={labBusy} onClick={startDeepCapture}>
                        {labBusy ? "Baking…" : "Record 30 min & Bake"}
                      </button>
                    ) : (
                      <button className="answer idk" onClick={stopCapture}>
                        Stop &amp; Bake now
                      </button>
                    )}
                    {recording && capRemaining !== null && (
                      <span className="tts-status">
                        {String(Math.floor(capRemaining / 60)).padStart(2, "0")}:
                        {String(capRemaining % 60).padStart(2, "0")} left
                      </span>
                    )}
                    <label className="meta-internal" title="Recommended — your recovery file if a bake hiccups">
                      <input
                        type="checkbox"
                        checked={deepSaveSrc}
                        onChange={(e) => setDeepSaveSrc(e.target.checked)}
                      />
                      Save source to Downloads
                    </label>
                    <span className="tts-status">{labStatus}</span>
                  </div>
                )}

                {labJob && (
                  <div className="bake-progress">
                    <div className="bake-steps">
                      {BAKE_STAGES.map((s, i) => {
                        const curIdx = BAKE_STAGES.findIndex((x) => x.key === labJob.stage);
                        const done = labJob.status === "done" || (curIdx >= 0 && i < curIdx);
                        const active = labJob.status === "running" && i === curIdx;
                        const errored = labJob.status === "error" && i === curIdx;
                        return (
                          <div
                            key={s.key}
                            className={
                              "bake-step" +
                              (done ? " done" : "") +
                              (active ? " active" : "") +
                              (errored ? " error" : "")
                            }
                          >
                            <span className="bake-dot" />
                            <span className="bake-step-label">{s.label}</span>
                          </div>
                        );
                      })}
                    </div>
                    <div className="bake-bar">
                      <div
                        className="bake-bar-fill"
                        style={{ width: `${Math.round((labJob.progress || 0) * 100)}%` }}
                      />
                    </div>
                    {(labJob.n_slices || labJob.n_samples) && (
                      <div className="voice-row-meta">
                        {labJob.n_slices ? `${labJob.n_slices} slices` : ""}
                        {labJob.n_samples ? ` · ${labJob.n_samples} samples` : ""}
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {tab === "listen" && (
          <div className="listen-panel">
            {(() => {
              // Voices actually present in the library, for the filter dropdown.
              const clipVoiceNames = Array.from(
                new Set(listenItems.map((it) => it.voice).filter(Boolean) as string[])
              ).sort((a, b) => a.localeCompare(b));
              const shown = listenItems.filter(
                (it) =>
                  (clipVoice === "all" || it.voice === clipVoice) &&
                  (!clipFavOnly || it.favorite)
              );
              return (
                <>
                  <div className="lib-filters">
                    <label className="lib-filter">
                      Voice
                      <select
                        className="voice-select"
                        value={clipVoice}
                        onChange={(e) => setClipVoice(e.target.value)}
                      >
                        <option value="all">All voices</option>
                        {clipVoiceNames.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="lib-filter">
                      Favorites
                      <label className="voice-toggle clip-fav-filter">
                        <input
                          type="checkbox"
                          checked={clipFavOnly}
                          onChange={(e) => setClipFavOnly(e.target.checked)}
                        />
                        ★ only
                      </label>
                    </div>
                    <span className="lib-count">
                      {shown.length} clip{shown.length === 1 ? "" : "s"}
                    </span>
                  </div>
                  {listenItems.length === 0 ? (
                    <div className="tts-status">No clips here yet.</div>
                  ) : shown.length === 0 ? (
                    <div className="tts-status">No clips match this filter.</div>
                  ) : (
                    shown.map((it) => (
                      <div className="tts-clip" key={it.file}>
                        <div className="tts-clip-head">
                          <button
                            className={`icon-btn clip-fav${it.favorite ? " on" : ""}`}
                            title={it.favorite ? "Remove from favorites" : "Mark as favorite"}
                            onClick={() => toggleFavorite(it.file, !it.favorite)}
                          >
                            <StarIcon filled={it.favorite} />
                          </button>
                          <span className="tts-clip-name">{it.file}</span>
                          {it.voice && <span className="tts-clip-meta">{it.voice}</span>}
                          <button
                            className="icon-btn"
                            title="Open the containing folder"
                            onClick={() => revealClip(it.file)}
                          >
                            <FolderIcon />
                          </button>
                          <button className="voice-del" onClick={() => deleteClip(it.file)}>
                            delete
                          </button>
                        </div>
                        {it.caption && <div className="listen-caption">{it.caption}</div>}
                        <audio
                          className="tts-audio"
                          src={`/api/listen/${encodeURIComponent(it.file)}`}
                          controls
                        />
                      </div>
                    ))
                  )}
                </>
              );
            })()}
          </div>
        )}

        {tab === "tools" && (
          <div className="tts-form">
            <div className="mode-seg">
              <button
                className={toolsTab === "capture" ? "mode-btn active" : "mode-btn"}
                onClick={() => setToolsTab("capture")}
              >
                Capture
              </button>
              <button
                className={toolsTab === "options" ? "mode-btn active" : "mode-btn"}
                onClick={() => setToolsTab("options")}
              >
                Options
              </button>
            </div>

            {toolsTab === "capture" && (
              <>
                <div className="emo-tag-hint">
                  Record audio from any browser tab and save it straight to your Downloads — no
                  cloning, no baking. Handy for grabbing a sample to keep or use elsewhere.
                </div>
                <div className="tool-list">
                  <div className="tool-row">
                    <div className="tool-text">
                      <div className="tool-name">Record &amp; stop</div>
                      <div className="tool-sub">Records until you click Stop, then saves.</div>
                    </div>
                    {recording ? (
                      <button className="answer idk" onClick={stopCapture}>Stop &amp; Save</button>
                    ) : (
                      <button className="answer go" onClick={() => toolCapture(null, "clip")}>Record</button>
                    )}
                  </div>
                  <div className="tool-row">
                    <div className="tool-text">
                      <div className="tool-name">15-second clip</div>
                      <div className="tool-sub">Records exactly 15 seconds, then saves.</div>
                    </div>
                    <button className="answer go" disabled={recording} onClick={() => toolCapture(15000, "15 sec")}>
                      Record 15s
                    </button>
                  </div>
                  <div className="tool-row">
                    <div className="tool-text">
                      <div className="tool-name">30-minute capture</div>
                      <div className="tool-sub">Records 30 minutes unattended, then saves.</div>
                    </div>
                    <button className="answer go" disabled={recording} onClick={() => toolCapture(30 * 60 * 1000, "30 min")}>
                      Record 30 min
                    </button>
                  </div>
                </div>
                {recording && capRemaining !== null && (
                  <div className="tts-status">
                    {String(Math.floor(capRemaining / 60)).padStart(2, "0")}:
                    {String(capRemaining % 60).padStart(2, "0")} left
                  </div>
                )}
                <div className="tts-status">{toolStatus}</div>
              </>
            )}

            {toolsTab === "options" && (
              <>
                <div className="emo-tag-hint">
                  Appearance and preferences. Changes apply instantly and are remembered.
                </div>
                <div className="tool-list">
                  <div className="tool-row">
                    <div className="tool-text">
                      <div className="tool-name">Appearance</div>
                      <div className="tool-sub">
                        Light, dark, or match your system.
                      </div>
                    </div>
                    <div className="mini-seg">
                      {MODES.map((m) => (
                        <button
                          key={m.id}
                          type="button"
                          className={mode === m.id ? "mini-btn active" : "mini-btn"}
                          onClick={() => setMode(m.id)}
                        >
                          {m.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="tool-row">
                    <div className="tool-text">
                      <div className="tool-name">Theme</div>
                      <div className="tool-sub">The overall color scheme.</div>
                    </div>
                    <select
                      className="theme-select"
                      value={theme}
                      title="Theme"
                      onChange={(e) => setTheme(e.target.value as ThemeId)}
                    >
                      {THEMES.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        </div>
      </div>

      {internalWarn && (
        <div className="modal-overlay" onClick={() => setInternalWarn(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">Internal-use-only voice</div>
            <div className="modal-body">
              <b>{voices.find((v) => v.id === voiceId)?.name}</b> is marked{" "}
              <b>internal use only</b> — it isn't cleared for distribution. Generate this
              speech anyway?
            </div>
            <div className="modal-actions">
              <button className="answer idk" onClick={() => setInternalWarn(false)}>
                Cancel
              </button>
              <button
                className="answer go"
                onClick={() => {
                  setInternalWarn(false);
                  doGenerate();
                }}
              >
                Generate anyway
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="elbow-bl" />
      <div className="bar-bottom">
        <span className="status-text">
          OpenVoice · Chatterbox · VoxCPM · self-hosted
        </span>
      </div>
    </div>
  );
}
