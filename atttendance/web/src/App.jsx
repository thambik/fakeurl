import { useEffect, useRef, useState } from "react";
import { Activity, Bell, BookOpen, Camera, Check, ChevronDown, CircleUserRound, Download, GraduationCap, LayoutDashboard, LogOut, Plus, ShieldCheck, Users, Video, X } from "lucide-react";
import { api, downloadReport } from "./services/api";

const initialForm = { email: "", full_name: "", password: "", student_number: "", class_id: "", parent_id: "", employee_code: "", name: "", section: "", teacher_id: "" };

function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError("");
    try { await onLogin(email, password); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return <main className="login-shell"><section className="login-card">
    <div className="brand-mark"><GraduationCap size={24} /></div><p className="eyebrow">SMARTATTEND AI</p>
    <h1>Welcome back</h1><p className="muted">Sign in to your school workspace.</p>
    <form onSubmit={submit} className="stack">
      <label>Email address<input autoComplete="username" type="email" required value={email} onChange={e => setEmail(e.target.value)} /></label>
      <label>Password<input autoComplete="current-password" type="password" required value={password} onChange={e => setPassword(e.target.value)} /></label>
      {error && <p className="error">{error}</p>}
      <button className="primary full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
    <p className="privacy-note"><ShieldCheck size={15} /> Biometric processing requires guardian consent.</p>
  </section></main>;
}

function App() {
  const [user, setUser] = useState(null);
  const [summary, setSummary] = useState(null);
  const [students, setStudents] = useState([]);
  const [classes, setClasses] = useState([]);
  const [teachers, setTeachers] = useState([]);
  const [parents, setParents] = useState([]);
  const [children, setChildren] = useState([]);
  const [history, setHistory] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [records, setRecords] = useState([]);
  const [pendingMatch, setPendingMatch] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState(initialForm);
  const [faceStudent, setFaceStudent] = useState("");
  const [faceImage, setFaceImage] = useState(null);
  const [busy, setBusy] = useState(false);
  const videoRef = useRef(null);
  const streamRef = useRef(null);

  useEffect(() => {
    const token = localStorage.getItem("smartattend_token");
    if (token) api("/auth/me").then(setUser).catch(() => localStorage.removeItem("smartattend_token"));
  }, []);

  async function loadDashboard() {
    const [sum, cls] = await Promise.all([api("/dashboard/summary"), api("/classes")]);
    setSummary(sum); setClasses(cls);
    if (["admin", "teacher"].includes(user?.role)) {
      setStudents(await api("/students"));
      if (user.role === "admin") {
        const [staff, guardians] = await Promise.all([api("/teachers"), api("/parents")]);
        setTeachers(staff); setParents(guardians);
      }
    } else {
      setHistory(await api("/attendance/history"));
      if (user?.role === "parent") setChildren(await api("/parents/children"));
    }
  }
  useEffect(() => { if (user) loadDashboard().catch(e => setError(e.message)); }, [user]);
  useEffect(() => () => streamRef.current?.getTracks().forEach(track => track.stop()), []);

  async function login(email, password) {
    const result = await api("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    localStorage.setItem("smartattend_token", result.access_token); setUser(result.user);
  }
  async function logout() {
    try { await api("/auth/logout", { method: "POST" }); } catch { /* local logout still clears the stateless token */ }
    streamRef.current?.getTracks().forEach(track => track.stop());
    localStorage.removeItem("smartattend_token"); setUser(null); setSummary(null); setActiveSession(null);
  }
  async function startSession(classId) {
    setError(""); setNotice("");
    try { const session = await api("/attendance/sessions", { method: "POST", body: JSON.stringify({ class_id: Number(classId) }) }); setActiveSession(session); setRecords([]); }
    catch (e) { setError(e.message); }
  }
  async function openCamera() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
      streamRef.current = stream; if (videoRef.current) videoRef.current.srcObject = stream;
    } catch (e) { setError(`Camera unavailable: ${e.message}`); }
  }
  async function captureFace() {
    if (!activeSession || !videoRef.current) return;
    const video = videoRef.current;
    if (!video.videoWidth) return setError("Camera is still starting.");
    const canvas = document.createElement("canvas"); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.88));
    const body = new FormData(); body.append("image", blob, "capture.jpg");
    setBusy(true); setError("");
    try { const match = await api(`/attendance/sessions/${activeSession.id}/recognize`, { method: "POST", body }); setPendingMatch(match); setNotice(match.detail); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function confirmPresent() {
    if (!pendingMatch?.student_id) return;
    try {
      await api(`/attendance/sessions/${activeSession.id}/manual`, { method: "POST", body: JSON.stringify({ student_id: pendingMatch.student_id, status: "present" }) });
      setNotice(`${pendingMatch.student_name} marked present.`); setPendingMatch(null);
    } catch (e) { setError(e.message); }
  }
  async function stopSession() {
    try { await api(`/attendance/sessions/${activeSession.id}/stop`, { method: "POST" }); setActiveSession(null); setPendingMatch(null); streamRef.current?.getTracks().forEach(track => track.stop()); await loadDashboard(); setNotice("Session closed; absences calculated."); }
    catch (e) { setError(e.message); }
  }
  async function submitEntity(kind, event) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      const body = { ...form };
      let path = `/${kind}`;
      if (kind === "classes") {
        body.teacher_id = body.teacher_id ? Number(body.teacher_id) : null;
      } else if (kind === "students") {
        body.class_id = Number(body.class_id); body.parent_id = body.parent_id ? Number(body.parent_id) : null;
      }
      async function enrollFace(event) {
        event.preventDefault(); setBusy(true); setError(""); setNotice("");
        try {
          if (!faceStudent || !faceImage) throw new Error("Choose a student and image.");
          const body = new FormData(); body.append("image", faceImage);
          await api(`/students/${faceStudent}/face-profile`, { method: "POST", body });
          setFaceImage(null); event.target.reset(); setNotice("Encrypted face profile enrolled.");
        } catch (e) { setError(e.message); } finally { setBusy(false); }
      }
      async function deleteStudent(studentId) {
        if (!window.confirm("Delete this student and their attendance/face profile records?")) return;
        try { await api(`/students/${studentId}`, { method: "DELETE" }); await loadDashboard(); setNotice("Student deleted."); }
        catch (e) { setError(e.message); }
      }
      await api(path, { method: "POST", body: JSON.stringify(body) });
      setForm(initialForm); await loadDashboard(); setNotice(`${kind.slice(0, -1)} created.`);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function consent(studentId, consent) {
    try { await api(`/parents/children/${studentId}/consent`, { method: "POST", body: JSON.stringify({ consent }) }); setNotice(consent ? "Consent recorded." : "Consent revoked and face profile removed."); }
    catch (e) { setError(e.message); }
  }
  if (!user) return <Login onLogin={login} />;

  const roleTitle = { admin: "Administrator", teacher: "Teacher", student: "Student", parent: "Parent" }[user.role];
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><GraduationCap size={21} /></div><div><b>SmartAttend</b><small>AI SCHOOL SUITE</small></div></div>
      <div className="school-label">WORKSPACE</div>
      <nav><div className="nav-item active"><LayoutDashboard size={18} /> Overview</div>
        {user.role === "admin" && <div className="nav-item"><Users size={18} /> People</div>}
        <div className="nav-item"><BookOpen size={18} /> Attendance</div>
      </nav>
      <div className="sidebar-bottom"><div className="user-mini"><div className="avatar">{user.full_name?.[0]}</div><div><b>{user.full_name}</b><small>{roleTitle}</small></div><button className="icon-btn" onClick={logout} title="Sign out"><LogOut size={17} /></button></div></div>
    </aside>
    <main className="main">
      <header className="topbar"><div><p className="eyebrow">{new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</p><h1>{user.role === "admin" ? "School overview" : `${roleTitle} dashboard`}</h1></div>
        <div className="top-actions"><span className="icon-btn" aria-label="Notifications"><Bell size={19} /></span><div className="profile-chip"><CircleUserRound size={20} /> {user.full_name}<ChevronDown size={15} /></div></div>
      </header>
      {error && <div className="alert error-alert"><X size={17} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
      {notice && <div className="alert success-alert"><Check size={17} />{notice}<button onClick={() => setNotice("")}><X size={15} /></button></div>}
      {user.role === "admin" && <AdminView summary={summary} students={students} teachers={teachers} parents={parents} classes={classes} form={form} setForm={setForm} submitEntity={submitEntity} busy={busy} onReport={() => downloadReport().catch(e => setError(e.message))} deleteStudent={deleteStudent} faceStudent={faceStudent} setFaceStudent={setFaceStudent} setFaceImage={setFaceImage} enrollFace={enrollFace} />}
      {user.role === "teacher" && <TeacherView classes={classes} students={students} activeSession={activeSession} startSession={startSession} videoRef={videoRef} openCamera={openCamera} captureFace={captureFace} busy={busy} pendingMatch={pendingMatch} confirmPresent={confirmPresent} stopSession={stopSession} refreshRecords={() => api(`/attendance/sessions/${activeSession.id}`)} onExport={classId => downloadReport(`?class_id=${classId}`).catch(e => setError(e.message))} />}
      {(user.role === "student" || user.role === "parent") && <FamilyView user={user} summary={summary} history={history} children={children} consent={consent} />}
    </main>
  </div>;
}

function Metric({ label, value, note, icon: Icon, tone = "" }) {
  return <article className="metric-card"><div className={`metric-icon ${tone}`}><Icon size={20} /></div><span className="metric-label">{label}</span><strong>{value ?? "—"}</strong><small>{note}</small></article>;
}

function AdminView({ summary, students, teachers, parents, classes, form, setForm, submitEntity, busy, onReport, deleteStudent, faceStudent, setFaceStudent, setFaceImage, enrollFace }) {
  const update = event => setForm(prev => ({ ...prev, [event.target.name]: event.target.value }));
  return <div className="content">
    <section className="metrics">
      <Metric label="Total students" value={summary?.students} note="Active student profiles" icon={GraduationCap} tone="blue" />
      <Metric label="Teaching staff" value={summary?.teachers} note="Registered teachers" icon={Users} tone="purple" />
      <Metric label="Present today" value={summary?.present_today} note="Across today's sessions" icon={Check} tone="green" />
      <Metric label="Attendance rate" value={`${summary?.attendance_percentage ?? 0}%`} note={`${summary?.absent_today ?? 0} absent today`} icon={Activity} tone="orange" />
    </section>
    <section className="section-card"><div className="section-heading"><div><h2>Attendance snapshot</h2><p>Today's recorded attendance</p></div><button className="secondary" onClick={onReport}><Download size={16} /> Export CSV</button></div>
      <div className="chart-area" style={{ height: 100 }}><div style={{ height: 12, borderRadius: 8, background: "#eef2f8", overflow: "hidden" }}><span style={{ display: "block", height: "100%", width: `${summary?.attendance_percentage ?? 0}%`, background: "#4775dd" }} /></div>
        <div className="chart-caption"><span>{summary?.present_today ?? 0} present · {summary?.absent_today ?? 0} absent</span><b>{summary?.attendance_percentage ?? 0}%</b></div></div>
    </section>
    <section className="section-card"><div className="section-heading"><div><h2>Student directory</h2><p>{students.length} enrolled students</p></div></div>
      {students.length ? <div className="table-wrap"><table><thead><tr><th>Student</th><th>Student ID</th><th>Class</th><th>Email</th><th></th></tr></thead><tbody>{students.map(s => <tr key={s.id}><td><div className="person"><span className="avatar soft">{s.full_name?.[0]}</span><b>{s.full_name}</b></div></td><td>{s.student_number}</td><td>{s.class_name || "—"}</td><td>{s.email}</td><td><button className="text-button" onClick={() => deleteStudent(s.id)}>Delete</button></td></tr>)}</tbody></table></div> : <div className="empty"><GraduationCap /><b>No students yet</b><span>Create your first student below.</span></div>}
    </section>
    <section className="grid-two">
      <section className="section-card"><div className="section-heading"><div><h2>Add student</h2><p>Creates an account and class enrollment.</p></div></div>
        <form className="form-grid" onSubmit={e => submitEntity("students", e)}>
          <label>Full name<input name="full_name" value={form.full_name} onChange={update} required /></label><label>Email<input name="email" type="email" value={form.email} onChange={update} required /></label>
          <label>Password (12+ chars)<input name="password" type="password" minLength="12" value={form.password} onChange={update} required /></label><label>Student number<input name="student_number" value={form.student_number} onChange={update} required /></label>
          <label>Class<select name="class_id" value={form.class_id} onChange={update} required><option value="">Select class</option>{classes.map(c => <option key={c.id} value={c.id}>{c.name} {c.section}</option>)}</select></label>
          <label>Parent / guardian<select name="parent_id" value={form.parent_id} onChange={update}><option value="">No parent linked</option>{parents.map(p => <option key={p.id} value={p.id}>{p.full_name} · ID {p.id}</option>)}</select></label>
          <button className="primary form-action" disabled={busy}><Plus size={16} /> Add student</button>
        </form>
      </section>
      <div className="stack">
        <section className="section-card"><div className="section-heading"><div><h2>Register parent</h2><p>Create a guardian account to link to students.</p></div></div>
          <form className="form-grid" onSubmit={e => submitEntity("parents", e)}>
            <label>Full name<input name="full_name" value={form.full_name} onChange={update} required /></label><label>Email<input name="email" type="email" value={form.email} onChange={update} required /></label>
            <label>Password (12+ chars)<input name="password" type="password" minLength="12" value={form.password} onChange={update} required /></label><label>Phone<input name="phone" value={form.phone || ""} onChange={update} /></label>
            <button className="primary form-action" disabled={busy}><Plus size={16} /> Add parent</button>
          </form>
        </section>
        <section className="section-card"><div className="section-heading"><div><h2>Create teacher</h2><p>Add staff access to the workspace.</p></div></div>
          <form className="form-grid" onSubmit={e => submitEntity("teachers", e)}>
            <label>Full name<input name="full_name" value={form.full_name} onChange={update} required /></label><label>Email<input name="email" type="email" value={form.email} onChange={update} required /></label>
            <label>Password (12+ chars)<input name="password" type="password" minLength="12" value={form.password} onChange={update} required /></label><label>Employee code<input name="employee_code" value={form.employee_code} onChange={update} required /></label>
            <button className="primary form-action" disabled={busy}><Plus size={16} /> Add teacher</button>
          </form>
        </section>
        <section className="section-card"><div className="section-heading"><div><h2>Create class</h2><p>Set up a class, section, and teacher.</p></div></div>
          <form className="form-grid" onSubmit={e => submitEntity("classes", e)}>
            <label>Class name<input name="name" value={form.name} onChange={update} required /></label><label>Section<input name="section" value={form.section} onChange={update} required /></label>
            <label>Teacher<select name="teacher_id" value={form.teacher_id} onChange={update}><option value="">Choose teacher</option>{teachers.map(t => <option key={t.id} value={t.id}>{t.full_name}</option>)}</select></label>
            <button className="primary form-action" disabled={busy}><Plus size={16} /> Create class</button>
          </form>
        </section>
      </div>
    </section>
    <section className="section-card"><div className="section-heading"><div><h2>Consent-gated face enrollment</h2><p>Embeddings are encrypted; raw enrollment images are not retained.</p></div></div>
      <form className="inline-form" onSubmit={enrollFace}>
        <select value={faceStudent} onChange={e => setFaceStudent(e.target.value)} required><option value="">Select student</option>{students.map(s => <option key={s.id} value={s.id}>{s.full_name} · {s.student_number}</option>)}</select>
        <input aria-label="Enrollment face image" type="file" accept="image/*" onChange={e => setFaceImage(e.target.files?.[0] || null)} required />
        <button className="primary" disabled={busy || !faceImage}><Camera size={15} /> Enroll face</button>
      </form>
    </section>
  </div>;
}

function TeacherView({ classes, students, activeSession, startSession, videoRef, openCamera, captureFace, busy, pendingMatch, confirmPresent, stopSession, refreshRecords, onExport }) {
  const [classId, setClassId] = useState("");
  const [records, setRecords] = useState([]);
  useEffect(() => { if (activeSession) api(`/attendance/sessions/${activeSession.id}`).then(setRecords).catch(() => setRecords([])); }, [activeSession]);
  const enrolled = students.filter(student => String(student.class_id) === String(activeSession?.class_id));
  return <div className="content">
    <section className="section-card"><div className="section-heading"><div><h2>Attendance session</h2><p>Start a class session, scan consented faces, or record manually.</p></div>
      {activeSession ? <button className="danger" onClick={stopSession}><X size={16} /> Stop session</button> : <div className="inline-form"><select value={classId} onChange={e => setClassId(e.target.value)}><option value="">Select assigned class</option>{classes.map(c => <option key={c.id} value={c.id}>{c.name} {c.section}</option>)}</select><button className="primary" disabled={!classId} onClick={() => startSession(classId)}><Plus size={16} /> Start</button></div>}
    </div>
      {!activeSession ? <div className="empty"><Camera /><b>No active session</b><span>Select one of your assigned classes to begin.</span></div> :
      <div className="teacher-grid"><div className="camera-panel"><div className="camera-window"><video ref={videoRef} autoPlay playsInline muted /><div className="camera-guide" /><div className="live-tag"><i /> SESSION LIVE</div></div>
        <div className="camera-actions"><button className="secondary" onClick={openCamera}><Video size={16} /> Open camera</button><button className="primary" onClick={captureFace} disabled={busy}><Camera size={16} /> {busy ? "Checking…" : "Capture & recognize"}</button></div>
        {pendingMatch?.matched && !pendingMatch.attendance_marked && <div className="match-card"><div><b>{pendingMatch.student_name}</b><small>Similarity {pendingMatch.similarity} · teacher confirmation required</small></div><button className="primary" onClick={async () => { await confirmPresent(); setRecords(await refreshRecords()); }}><Check size={15} /> Confirm present</button></div>}
      </div>
      <div className="session-roster"><div className="roster-head"><h3>Session roster</h3><span>{records.length} marked</span></div>
        {enrolled.map(s => { const row = records.find(r => r.student_id === s.id); return <div className="roster-row" key={s.id}><span className="avatar soft">{s.full_name[0]}</span><div><b>{s.full_name}</b><small>{s.student_number}</small></div>{row ? <span className={`pill ${row.status}`}>{row.status}</span> : <button className="tiny-button" onClick={async () => { try { await api(`/attendance/sessions/${activeSession.id}/manual`, { method: "POST", body: JSON.stringify({ student_id: s.id, status: "present" }) }); setRecords(await refreshRecords()); } catch (e) { alert(e.message); } }}>Mark present</button>}</div>; })}
        {!enrolled.length && <div className="empty small-empty">No enrolled students found for this class.</div>}
      </div></div>}
    </section>
    <section className="section-card"><div className="section-heading"><div><h2>Class reports</h2><p>Export attendance percentages for your assigned classes.</p></div></div>
      {classes.map(item => <div className="child-row" key={item.id}><div className="person"><BookOpen size={18} /><div><b>{item.name} · {item.section}</b><small>Attendance sessions and totals</small></div></div><button className="secondary" onClick={() => onExport(item.id)}><Download size={15} /> Export CSV</button></div>)}
      {!classes.length && <div className="empty small-empty">No assigned classes available.</div>}
    </section>
    <div className="notice-box"><ShieldCheck size={18} /><span><b>Biometric privacy</b> — only students with recorded guardian consent can be matched. Face matching does not verify liveness; review detections before confirming attendance.</span></div>
  </div>;
}

function FamilyView({ user, summary, history, children, consent }) {
  const subject = user.role === "student" ? summary?.children?.[0] : null;
  const data = user.role === "student" ? history : history;
  const [selectedChild, setSelectedChild] = useState("");
  const childHistory = data.filter(item => !selectedChild || String(item.student_id) === selectedChild);
  const percent = subject?.percentage ?? (childHistory.length ? Math.round(childHistory.filter(r => ["present", "late"].includes(r.status)).length * 100 / childHistory.length) : 0);
  const today = new Date();
  const firstWeekday = (new Date(today.getFullYear(), today.getMonth(), 1).getDay() + 6) % 7;
  const daysInMonth = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  const dates = new Map();
  childHistory.forEach(record => {
    const key = String(record.date).slice(0, 10);
    if (key.startsWith(`${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-`)) {
      const current = dates.get(key);
      dates.set(key, current === "present" || current === "late" || ["present", "late"].includes(record.status) ? "present" : record.status);
    }
  });
  return <div className="content">
    {user.role === "parent" && <section className="section-card"><div className="section-heading"><div><h2>Linked children</h2><p>Manage child selection and biometric consent.</p></div>{children.length > 0 && <select value={selectedChild} onChange={e => setSelectedChild(e.target.value)}><option value="">All children</option>{children.map(c => <option key={c.id} value={c.id}>{c.full_name}</option>)}</select>}</div>
      {!children.length && <div className="empty"><Users /><b>No linked children</b><span>Ask your school administrator to link your parent account.</span></div>}
      {children.filter(c => !selectedChild || String(c.id) === selectedChild).map(child => <div className="child-row" key={child.id}><div className="person"><span className="avatar soft">{child.full_name[0]}</span><div><b>{child.full_name}</b><small>{child.student_number}</small></div></div><button className="secondary" onClick={() => consent(child.id, true)}>Grant face consent</button><button className="text-button" onClick={() => consent(child.id, false)}>Revoke</button></div>)}
    </section>}
    <section className="metrics"><Metric label="Attendance rate" value={`${percent}%`} note="Based on recorded sessions" icon={Activity} tone="blue" /><Metric label={user.role === "parent" ? "Linked children" : "Recent records"} value={user.role === "parent" ? children.length : history.length} note="Attendance records available" icon={GraduationCap} tone="green" /></section>
    <section className="section-card"><div className="section-heading"><div><h2>Attendance calendar</h2><p>{today.toLocaleDateString(undefined, { month: "long", year: "numeric" })} · recent session status</p></div></div>
      <div className="calendar-grid">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => <b className="calendar-label" key={day}>{day}</b>)}
        {Array.from({ length: firstWeekday }, (_, index) => <span className="calendar-empty" key={`empty-${index}`} />)}
        {Array.from({ length: daysInMonth }, (_, index) => {
          const number = index + 1;
          const key = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(number).padStart(2, "0")}`;
          const status = dates.get(key);
          return <span className={`calendar-day ${status || ""}`} key={key} title={status ? `${status} attendance` : "No recorded session"}>{number}</span>;
        })}
      </div>
      <div className="calendar-legend"><span><i className="present-dot" /> Present</span><span><i className="absent-dot" /> Absent</span></div>
    </section>
    <section className="section-card"><div className="section-heading"><div><h2>Attendance history</h2><p>Recent attendance sessions and status.</p></div></div>
      {childHistory.length ? <div className="table-wrap"><table><thead><tr><th>Student</th><th>Date</th><th>Status</th><th>Recorded</th></tr></thead><tbody>{childHistory.map(row => <tr key={row.id}><td>{row.student_name}</td><td>{new Date(row.date).toLocaleDateString()}</td><td><span className={`pill ${row.status}`}>{row.status}</span></td><td>{new Date(row.recorded_at).toLocaleTimeString()}</td></tr>)}</tbody></table></div> : <div className="empty"><BookOpen /><b>No attendance records</b><span>Records appear when class sessions are completed.</span></div>}
    </section>
  </div>;
}

export default App;

