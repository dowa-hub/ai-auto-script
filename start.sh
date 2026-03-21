#!/usr/bin/env bash
# Start AI Auto Script — backend + frontend dev servers
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Backend ───────────────────────────────────────────────────────────────────
BACKEND="$ROOT/backend"

if [ ! -d "$BACKEND/.venv" ]; then
  echo "📦 Creating Python virtual environment…"
  python3 -m venv "$BACKEND/.venv"
fi

source "$BACKEND/.venv/bin/activate"

echo "📦 Installing backend dependencies…"
pip install -q -r "$BACKEND/requirements.txt"

if [ ! -f "$BACKEND/.env" ]; then
  cp "$BACKEND/.env.example" "$BACKEND/.env"
  echo "✅ Created backend/.env from example"
fi

echo "🚀 Starting backend on http://localhost:8000 …"
cd "$BACKEND"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# ── Frontend ──────────────────────────────────────────────────────────────────
FRONTEND="$ROOT/frontend"

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "📦 Installing frontend dependencies…"
  cd "$FRONTEND" && npm install
fi

echo "🚀 Starting frontend on http://localhost:5173 …"
cd "$FRONTEND"
npm run dev &
FRONTEND_PID=$!

# ── Open browser ──────────────────────────────────────────────────────────────
sleep 3
open "http://localhost:5173" 2>/dev/null || true

echo ""
echo "✅ AI Auto Script running!"
echo "   Frontend → http://localhost:5173"
echo "   Backend  → http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop both servers."

# Wait and clean up on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
