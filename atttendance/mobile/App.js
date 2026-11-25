import React, { useEffect, useRef, useState } from "react";
import { ActivityIndicator, SafeAreaView, ScrollView, StatusBar, StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";

const API_URL = process.env.EXPO_PUBLIC_API_URL || "http://localhost:8000/api/v1";
let accessToken = null;

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(`${API_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function Button({ title, onPress, secondary = false, disabled = false }) {
  return <TouchableOpacity disabled={disabled} onPress={onPress} style={[styles.button, secondary && styles.secondary, disabled && styles.disabled]}><Text style={[styles.buttonText, secondary && styles.secondaryText]}>{title}</Text></TouchableOpacity>;
}

export default function App() {
  const [user, setUser] = useState(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [summary, setSummary] = useState(null);
  const [classes, setClasses] = useState([]);
  const [students, setStudents] = useState([]);
  const [children, setChildren] = useState([]);
  const [history, setHistory] = useState([]);
  const [records, setRecords] = useState([]);
  const [selectedClass, setSelectedClass] = useState(null);
  const [session, setSession] = useState(null);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [match, setMatch] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const camera = useRef(null);
  const [permission, requestPermission] = useCameraPermissions();

  async function load() {
    const [stats, classList] = await Promise.all([request("/dashboard/summary"), request("/classes")]);
    setSummary(stats); setClasses(classList);
    if (user.role === "teacher") setStudents(await request("/students"));
    if (user.role === "parent") {
      setChildren(await request("/parents/children"));
      setHistory(await request("/attendance/history"));
    }
    if (user.role === "student") setHistory(await request("/attendance/history"));
  }
  useEffect(() => { if (user) load().catch(e => setError(e.message)); }, [user]);

  async function signIn() {
    setBusy(true); setError("");
    try {
      const result = await request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      accessToken = result.access_token; setUser(result.user);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function signOut() {
    if (accessToken) { try { await request("/auth/logout", { method: "POST" }); } catch { /* clear local token regardless */ } }
    accessToken = null; setUser(null); setSession(null); setCameraOpen(false);
  }
  async function startSession(item) {
    setError(""); setMessage("");
    try {
      const created = await request("/attendance/sessions", { method: "POST", body: JSON.stringify({ class_id: item.id }) });
      setSelectedClass(item); setSession(created); setRecords([]); setMessage(`Attendance is live for ${item.name} ${item.section}.`);
    } catch (e) { setError(e.message); }
  }
  async function capture() {
    if (!camera.current || !session) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const picture = await camera.current.takePictureAsync({ quality: 0.8 });
      const body = new FormData();
      body.append("image", { uri: picture.uri, name: "attendance.jpg", type: "image/jpeg" });
      const result = await request(`/attendance/sessions/${session.id}/recognize`, { method: "POST", body });
      setMatch(result); setMessage(result.detail);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function confirmMatch() {
    if (!match?.student_id) return;
    try {
      await request(`/attendance/sessions/${session.id}/manual`, { method: "POST", body: JSON.stringify({ student_id: match.student_id, status: "present" }) });
      setRecords(await request(`/attendance/sessions/${session.id}`)); setMessage(`${match.student_name} marked present.`); setMatch(null);
    } catch (e) { setError(e.message); }
  }
  async function markPresent(studentId) {
    try { await request(`/attendance/sessions/${session.id}/manual`, { method: "POST", body: JSON.stringify({ student_id: studentId, status: "present" }) }); setRecords(await request(`/attendance/sessions/${session.id}`)); setMessage("Attendance recorded."); }
    catch (e) { setError(e.message); }
  }
  async function stopSession() {
    try { await request(`/attendance/sessions/${session.id}/stop`, { method: "POST" }); setSession(null); setCameraOpen(false); setMessage("Session closed. Absences have been calculated."); await load(); }
    catch (e) { setError(e.message); }
  }
  async function giveConsent(studentId, consent) {
    try { await request(`/parents/children/${studentId}/consent`, { method: "POST", body: JSON.stringify({ consent }) }); setMessage(consent ? "Guardian consent recorded." : "Consent revoked; stored face profile deleted."); }
    catch (e) { setError(e.message); }
  }
  const displayName = user?.full_name || "";

  return <SafeAreaView style={styles.safe}>
    <StatusBar barStyle="dark-content" backgroundColor="#f4f7fc" />
    {!user ? <View style={styles.loginWrap}><View style={styles.loginCard}>
      <View style={styles.logo}><Text style={styles.logoText}>S</Text></View><Text style={styles.brand}>SmartAttend AI</Text><Text style={styles.subtitle}>School attendance, made clear.</Text>
      <Text style={styles.label}>Email</Text><TextInput style={styles.input} autoCapitalize="none" keyboardType="email-address" value={email} onChangeText={setEmail} placeholder="you@school.edu" />
      <Text style={styles.label}>Password</Text><TextInput style={styles.input} secureTextEntry value={password} onChangeText={setPassword} placeholder="Enter your password" />
      {error ? <Text style={styles.error}>{error}</Text> : null}<Button title={busy ? "Signing in..." : "Sign in"} onPress={signIn} disabled={busy || !email || !password} />
      <Text style={styles.disclaimer}>Face data is processed only with guardian consent.</Text>
    </View></View> : <ScrollView contentContainerStyle={styles.page}>
      <View style={styles.header}><View><Text style={styles.kicker}>{user.role.toUpperCase()} WORKSPACE</Text><Text style={styles.heading}>Hello, {displayName.split(" ")[0]}</Text></View><TouchableOpacity onPress={signOut}><Text style={styles.link}>Sign out</Text></TouchableOpacity></View>
      {error ? <Text style={styles.errorBox}>{error}</Text> : null}
      {message ? <Text style={styles.successBox}>{message}</Text> : null}
      {user.role === "admin" && <><Text style={styles.sectionTitle}>School overview</Text><View style={styles.stats}>
        <Stat label="Students" value={summary?.students} /><Stat label="Teachers" value={summary?.teachers} /><Stat label="Present today" value={summary?.present_today} /><Stat label="Attendance" value={`${summary?.attendance_percentage ?? 0}%`} />
      </View><Text style={styles.helper}>Use the web dashboard for student, staff, class and report administration.</Text></>}
      {user.role === "teacher" && <>
        <Text style={styles.sectionTitle}>Assigned classes</Text>
        {!session && classes.map(item => <View style={styles.listCard} key={item.id}><View><Text style={styles.cardTitle}>{item.name} · {item.section}</Text><Text style={styles.cardSub}>Start a new attendance session</Text></View><Button title="Start" onPress={() => startSession(item)} /></View>)}
        {session && <View style={styles.card}>
          <View style={styles.row}><View><Text style={styles.cardTitle}>{selectedClass?.name} · {selectedClass?.section}</Text><Text style={styles.cardSub}>Session #{session.id}</Text></View><Button title="Stop session" secondary onPress={stopSession} /></View>
          {cameraOpen ? <><View style={styles.camera}><CameraView ref={camera} style={StyleSheet.absoluteFill} facing="front" /></View>
            <Button title={busy ? "Checking..." : "Capture and recognize"} disabled={busy} onPress={capture} />
            {match?.matched && !match.attendance_marked && <View style={styles.matchCard}><Text style={styles.cardTitle}>{match.student_name} · {match.similarity}</Text><Button title="Confirm present" onPress={confirmMatch} /></View>}
          </> : <Button title="Open attendance camera" onPress={async () => { if (!permission?.granted) { const result = await requestPermission(); if (!result.granted) return setError("Camera permission is required."); } setCameraOpen(true); }} />}
          <Text style={styles.rosterTitle}>Manual attendance</Text>
          {students.filter(s => s.class_id === selectedClass?.id).map(s => { const record = records.find(row => row.student_id === s.id); return <View style={styles.roster} key={s.id}><View><Text style={styles.cardTitle}>{s.full_name}</Text><Text style={styles.cardSub}>{s.student_number}</Text></View>{record ? <Text style={[styles.status, styles.present]}>{record.status}</Text> : <TouchableOpacity onPress={() => markPresent(s.id)}><Text style={styles.link}>Mark present</Text></TouchableOpacity>}</View>; })}
        </View>}
        <Text style={styles.disclaimer}>Automatic marking is disabled unless configured by the school. Review each match; face matching does not verify liveness.</Text>
      </>}
      {(user.role === "student" || user.role === "parent") && <>
        {user.role === "parent" && <><Text style={styles.sectionTitle}>Linked children</Text>{children.length ? children.map(child => <View style={styles.listCard} key={child.id}><View><Text style={styles.cardTitle}>{child.full_name}</Text><Text style={styles.cardSub}>{child.student_number}</Text></View><View style={styles.consentButtons}><TouchableOpacity onPress={() => giveConsent(child.id, true)}><Text style={styles.link}>Consent</Text></TouchableOpacity><TouchableOpacity onPress={() => giveConsent(child.id, false)}><Text style={styles.revoke}>Revoke</Text></TouchableOpacity></View></View>) : <Text style={styles.helper}>No children linked. Ask the school administrator for help.</Text>}</>}
        <Text style={styles.sectionTitle}>Attendance history</Text>
        {history.length ? history.map(record => <View style={styles.listCard} key={record.id}><View><Text style={styles.cardTitle}>{user.role === "parent" ? record.student_name : record.date}</Text><Text style={styles.cardSub}>{new Date(record.date).toLocaleDateString()} · {record.source}</Text></View><Text style={[styles.status, record.status === "present" ? styles.present : styles.absent]}>{record.status}</Text></View>) : <View style={styles.card}><Text style={styles.helper}>No attendance records yet.</Text></View>}
      </>}
    </ScrollView>}
  </SafeAreaView>;
}

function Stat({ label, value }) {
  return <View style={styles.stat}><Text style={styles.statValue}>{value ?? "—"}</Text><Text style={styles.statLabel}>{label}</Text></View>;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#f4f7fc" },
  page: { padding: 20, paddingBottom: 42, gap: 14 },
  loginWrap: { flex: 1, justifyContent: "center", padding: 22 },
  loginCard: { backgroundColor: "#fff", borderRadius: 18, padding: 24, elevation: 3 },
  logo: { width: 44, height: 44, borderRadius: 13, backgroundColor: "#eaf1ff", alignItems: "center", justifyContent: "center", marginBottom: 17 },
  logoText: { color: "#376bdb", fontSize: 24, fontWeight: "800" },
  brand: { color: "#17283e", fontSize: 22, fontWeight: "800" },
  subtitle: { color: "#8291a5", marginTop: 6, marginBottom: 24 },
  label: { color: "#66788f", fontSize: 12, fontWeight: "700", marginBottom: 6 },
  input: { height: 46, backgroundColor: "#fff", borderWidth: 1, borderColor: "#e2e8f0", borderRadius: 9, paddingHorizontal: 12, marginBottom: 15 },
  button: { minHeight: 42, paddingHorizontal: 14, borderRadius: 9, backgroundColor: "#376bdb", alignItems: "center", justifyContent: "center", marginTop: 6 },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 13 },
  secondary: { backgroundColor: "#eef2f8" },
  secondaryText: { color: "#52647b" },
  disabled: { opacity: 0.5 },
  disclaimer: { color: "#8a98ab", fontSize: 11, lineHeight: 17, marginTop: 14 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 5 },
  kicker: { color: "#7590c7", fontSize: 10, fontWeight: "800", letterSpacing: 1.2 },
  heading: { color: "#17283e", fontSize: 24, fontWeight: "800", marginTop: 5 },
  link: { color: "#376bdb", fontWeight: "700", fontSize: 12 },
  revoke: { color: "#b45650", fontWeight: "700", fontSize: 12 },
  sectionTitle: { color: "#20344c", fontSize: 17, fontWeight: "800", marginTop: 13, marginBottom: 1 },
  stats: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  stat: { width: "48%", minHeight: 84, backgroundColor: "#fff", borderWidth: 1, borderColor: "#e8edf4", borderRadius: 12, padding: 14 },
  statValue: { color: "#203a5c", fontSize: 22, fontWeight: "800" },
  statLabel: { color: "#8391a4", fontSize: 11, marginTop: 5 },
  card: { backgroundColor: "#fff", borderWidth: 1, borderColor: "#e8edf4", borderRadius: 12, padding: 14, gap: 10 },
  listCard: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12, padding: 14, borderRadius: 11, backgroundColor: "#fff", borderWidth: 1, borderColor: "#e8edf4" },
  cardTitle: { color: "#253b55", fontWeight: "700", fontSize: 13 },
  cardSub: { color: "#8b99aa", fontSize: 11, marginTop: 4 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  camera: { height: 360, borderRadius: 12, overflow: "hidden", backgroundColor: "#111c2c" },
  matchCard: { borderWidth: 1, borderColor: "#dce8fc", backgroundColor: "#f4f8ff", padding: 12, borderRadius: 10 },
  rosterTitle: { color: "#2b405b", fontWeight: "800", marginTop: 8 },
  roster: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", borderTopWidth: 1, borderTopColor: "#eef1f5", paddingVertical: 11 },
  consentButtons: { flexDirection: "row", gap: 12 },
  status: { fontSize: 11, textTransform: "capitalize", fontWeight: "800" },
  present: { color: "#238161" },
  absent: { color: "#bd5b54" },
  helper: { color: "#8492a5", fontSize: 12, lineHeight: 18 },
  error: { color: "#b34842", fontSize: 12, marginBottom: 10 },
  errorBox: { padding: 12, backgroundColor: "#fff0ef", borderRadius: 9, color: "#a94640", fontSize: 12 },
  successBox: { padding: 12, backgroundColor: "#eaf8f1", borderRadius: 9, color: "#247d5d", fontSize: 12 },
});

