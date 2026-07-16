"use client";

import { useAuth } from "@/lib/firebase/auth-context";

export default function Header() {
  const { user, loading, isFirebaseReady, signInWithGoogle, signOut } =
    useAuth();

  return (
    <header className="header">
      <div className="header__left">
        <h1 className="header__logo">
          <span className="header__logo-icon">◈</span>
          TradingFirm
        </h1>
        <span className="header__badge">SCANNER</span>
      </div>

      <div className="header__right">
        <div className="header__market-status">
          <span className="header__dot header__dot--closed" id="market-dot" />
          <span className="header__market-label">Market Closed</span>
        </div>

        {!loading && isFirebaseReady && (
          <>
            {user ? (
              <div className="header__user">
                <img
                  src={user.photoURL || ""}
                  alt=""
                  className="header__avatar"
                  referrerPolicy="no-referrer"
                />
                <span className="header__username">
                  {user.displayName?.split(" ")[0]}
                </span>
                <button onClick={signOut} className="header__btn header__btn--ghost">
                  Sign Out
                </button>
              </div>
            ) : (
              <button
                onClick={signInWithGoogle}
                className="header__btn header__btn--primary"
              >
                Sign In
              </button>
            )}
          </>
        )}
      </div>

      <style jsx>{`
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 14px 28px;
          background: var(--bg-secondary);
          border-bottom: 1px solid var(--border-color);
          position: sticky;
          top: 0;
          z-index: 100;
          backdrop-filter: blur(12px);
        }

        .header__left {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .header__logo {
          font-size: 1.25rem;
          font-weight: 700;
          background: linear-gradient(135deg, #448aff, #00e676);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .header__logo-icon {
          font-size: 1.4rem;
        }

        .header__badge {
          font-size: 0.6rem;
          font-weight: 700;
          letter-spacing: 0.12em;
          padding: 3px 8px;
          border-radius: 4px;
          background: var(--accent-blue-dim);
          color: var(--accent-blue);
        }

        .header__right {
          display: flex;
          align-items: center;
          gap: 20px;
        }

        .header__market-status {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 0.8rem;
          color: var(--text-secondary);
        }

        .header__dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
        }

        .header__dot--open {
          background: var(--accent-green);
          box-shadow: 0 0 8px var(--accent-green);
          animation: pulse 2s infinite;
        }

        .header__dot--closed {
          background: var(--text-muted);
        }

        .header__user {
          display: flex;
          align-items: center;
          gap: 10px;
        }

        .header__avatar {
          width: 28px;
          height: 28px;
          border-radius: 50%;
          border: 1px solid var(--border-color-hover);
        }

        .header__username {
          font-size: 0.85rem;
          color: var(--text-secondary);
        }

        .header__btn {
          padding: 6px 16px;
          border-radius: var(--radius-sm);
          font-size: 0.8rem;
          font-weight: 600;
          cursor: pointer;
          transition: all var(--transition-fast);
          border: none;
          font-family: inherit;
        }

        .header__btn--primary {
          background: var(--accent-blue);
          color: #fff;
        }

        .header__btn--primary:hover {
          opacity: 0.9;
          transform: translateY(-1px);
        }

        .header__btn--ghost {
          background: transparent;
          color: var(--text-muted);
          border: 1px solid var(--border-color);
        }

        .header__btn--ghost:hover {
          color: var(--text-secondary);
          border-color: var(--border-color-hover);
        }

        @keyframes pulse {
          0%,
          100% {
            opacity: 1;
          }
          50% {
            opacity: 0.4;
          }
        }
      `}</style>
    </header>
  );
}
