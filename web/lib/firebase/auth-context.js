"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { isConfigured, auth } from "@/lib/firebase/config";

const AuthContext = createContext({
  user: null,
  loading: true,
  isFirebaseReady: false,
  signInWithGoogle: async () => {},
  signOut: async () => {},
});

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isConfigured || !auth) {
      setLoading(false);
      return;
    }

    // Dynamic import to avoid SSR issues
    import("firebase/auth").then(({ onAuthStateChanged }) => {
      const unsubscribe = onAuthStateChanged(auth, (firebaseUser) => {
        setUser(firebaseUser);
        setLoading(false);
      });
      return () => unsubscribe();
    });
  }, []);

  const signInWithGoogle = async () => {
    if (!isConfigured || !auth) return;
    const { signInWithPopup, GoogleAuthProvider } = await import(
      "firebase/auth"
    );
    const provider = new GoogleAuthProvider();
    try {
      await signInWithPopup(auth, provider);
    } catch (error) {
      console.error("Google sign-in error:", error);
    }
  };

  const signOut = async () => {
    if (!isConfigured || !auth) return;
    const { signOut: firebaseSignOut } = await import("firebase/auth");
    try {
      await firebaseSignOut(auth);
    } catch (error) {
      console.error("Sign-out error:", error);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isFirebaseReady: isConfigured,
        signInWithGoogle,
        signOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
