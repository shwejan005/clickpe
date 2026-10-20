import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { GoogleAuthProvider, getAuth, inMemoryPersistence, setPersistence, signInWithPopup, signOut, type User } from "firebase/auth";
import { initializeApp, getApps } from "firebase/app";
import { apiJson, refreshCsrfToken, setWorkspaceId } from "../lib/api";

type Profile = {
  user: { id: number; email: string; display_name: string | null };
  workspace: { id: string; name: string } | null;
  workspaces: { id: string; name: string; role: string }[];
  workspace_role: string;
  platform_role: string | null;
  capabilities: { can_upload: boolean; can_manage_workspace: boolean; can_review_criteria: boolean };
};

type AuthContextValue = {
  profile: Profile | null;
  loading: boolean;
  firebaseReady: boolean;
  authError: string;
  signIn: () => Promise<void>;
  signOutUser: () => Promise<void>;
  reloadProfile: () => Promise<void>;
  switchWorkspace: (workspaceId: string) => Promise<void>;
};

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};
const firebaseReady = Object.values(firebaseConfig).every(Boolean);
const firebaseApp = firebaseReady ? (getApps()[0] || initializeApp(firebaseConfig)) : null;
const firebaseAuth = firebaseApp ? getAuth(firebaseApp) : null;

const AuthContext = createContext<AuthContextValue | null>(null);

async function exchangeForSession(user: User) {
  const csrf = await refreshCsrfToken();
  const idToken = await user.getIdToken(true);
  await apiJson("/api/auth/session", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify({ id_token: idToken }),
  });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState("");

  const reloadProfile = useCallback(async () => {
    const value = await apiJson<Profile>("/api/me");
    setProfile(value);
    setAuthError("");
  }, []);

  const switchWorkspace = useCallback(async (workspaceId: string) => {
    if (!profile?.workspaces.some((workspace) => workspace.id === workspaceId)) throw new Error("You do not have access to that workspace.");
    const previous = profile.workspace?.id || "";
    setWorkspaceId(workspaceId);
    try {
      await reloadProfile();
    } catch (error) {
      setWorkspaceId(previous);
      throw error;
    }
  }, [profile, reloadProfile]);

  useEffect(() => {
    let mounted = true;
    async function restore() {
      try {
        await refreshCsrfToken();
        const value = await apiJson<Profile>("/api/me");
        if (mounted) { setProfile(value); setWorkspaceId(value.workspace?.id || ""); }
      } catch {
        if (mounted) { setProfile(null); setWorkspaceId(""); }
      } finally {
        if (mounted) setLoading(false);
      }
    }
    void restore();
    return () => { mounted = false; };
  }, []);

  const signIn = useCallback(async () => {
    setAuthError("");
    if (!firebaseAuth) throw new Error("Firebase web configuration is missing. Add the VITE_FIREBASE_* values to .env and restart the frontend.");
    try {
      await setPersistence(firebaseAuth, inMemoryPersistence);
      const credential = await signInWithPopup(firebaseAuth, new GoogleAuthProvider());
      await exchangeForSession(credential.user);
      await signOut(firebaseAuth);
      await reloadProfile();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Firebase sign-in failed.";
      setAuthError(message);
      throw error;
    }
  }, [reloadProfile]);

  const signOutUser = useCallback(async () => {
    setAuthError("");
    try {
      if (!document.cookie.split("; ").some((item) => item.startsWith("clickpe_csrf="))) await refreshCsrfToken();
      await apiJson("/api/auth/logout", { method: "POST" });
      if (firebaseAuth) await signOut(firebaseAuth);
      setProfile(null); setWorkspaceId("");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Sign-out failed.");
      throw error;
    }
  }, []);

  const value = useMemo(() => ({ profile, loading, firebaseReady, authError, signIn, signOutUser, reloadProfile, switchWorkspace }),
    [profile, loading, authError, signIn, signOutUser, reloadProfile, switchWorkspace]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}

