"""
MOP Extraction Agent - Web Extension

Flask web interface that extends the existing MOP extraction agent/pipeline in this repo.
Provides a ChatGPT-style Q&A interface for querying the MOPs synthesis knowledge graph.
"""

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from dotenv import load_dotenv
import asyncio
import os
import sys
from pathlib import Path
from datetime import datetime
import uuid
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError

# Load environment variables from .env file
load_dotenv()

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mini_marie.marie_agent import MarieAgent
from src.utils.global_logger import get_logger

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "mop-extraction-web-dev-secret-key-change-in-production")
CORS(app)

logger = get_logger("webapp", "MOPExtractionWebApp")

# Last-turn memory store (server-side) keyed by session_id.
# Stores only the most recent (question, answer) to keep behavior predictable.
_last_turn_by_session: dict[str, dict[str, str]] = {}
_last_turn_lock = threading.Lock()

# -----------------------------------------------------------------------------
# Async execution model
#
# DO NOT create/close a new event loop per request.
# Some async transports (httpx/OpenAI, anyio) may cache resources across calls.
# If those resources were created under a loop that later gets closed, subsequent
# requests can fail with "RuntimeError: Event loop is closed".
#
# Instead, run a single, long-lived asyncio loop in a background thread and
# submit coroutines to it.
# -----------------------------------------------------------------------------

_async_loop: asyncio.AbstractEventLoop | None = None
_async_thread: threading.Thread | None = None
_async_lock = threading.Lock()


def _loop_thread_target(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def get_async_loop() -> asyncio.AbstractEventLoop:
    global _async_loop, _async_thread
    if _async_loop is not None:
        return _async_loop
    with _async_lock:
        if _async_loop is not None:
            return _async_loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=_loop_thread_target, args=(loop,), daemon=True)
        thread.start()
        _async_loop = loop
        _async_thread = thread
        logger.info("Started background asyncio loop thread for web requests")
        return _async_loop


def run_async(coro, timeout: float | None = None):
    """Run a coroutine on the shared background event loop and wait for result."""
    loop = get_async_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        future.cancel()
        raise

# Preset example questions
EXAMPLE_QUESTIONS = [
    {
        "category": "Synthesis Recipes",
        "questions": [
            "What is the complete recipe for synthesis of VMOP-17?",
            "What chemicals are needed to synthesize UMC-2?",
            "What is the synthesis procedure for CIAC-105?",
            "How do I synthesize IRMOP-50?",
            "How long does it take in total to prepare VMOP-17?"
        ]
    },
    {
        "category": "Synthesis Conditions",
        "questions": [
            "What temperature is used in the VMOP-17 synthesis?",
            "What are the reaction conditions for UMC-2?",
            "How long does the CIAC-105 synthesis take?",
            "What are the synthesis steps for IRMOP-50?",
            "Which synthesis of MOP used longest time?",
            "Synthesis with highest temperature?"
        ]
    },
    {
        "category": "MOPs Information",
        "questions": [
            "What is the CCDC number for CIAC-105?",
            "What are the chemical building units of VMOP-17?",
            "What is the formula of UMC-2?",
            "Tell me about MOP-54",
            "List all the MOPs you know"
        ]
    },
    {
        "category": "Characterisation",
        "questions": [
            "What do you know about the characterisation of VMOP-1?",
            "What do you know about the characterisation of VMOP-17?"
        ]
    },
    {
        "category": "Filters & Constraints",
        "questions": [
            "Give me the list of MOPs where no heating step is involved.",
            "I wonder which MOP synthesis takes the longest time."
        ]
    },
    {
        "category": "CBU Analysis",
        "questions": [
            "What is the most commonly used metal CBU?",
            "Top 5 metal CBU and top 5 organic CBU?",
            "Recipe of Synthesis where Zr are used in metal CBU"
        ]
    },
    {
        "category": "IR / Spectroscopy",
        "questions": [
            "What material are commonly used for Infrared (IR) Spectroscopy?",
            "What IR Materials are used in synthesis?"
        ]
    },
    {
        "category": "Comparative Analysis",
        "questions": [
            "Compare the syntheses of UMC-2 and UMC-3",
            "What are the differences between VMOP-17 and IRMOP-50?",
            "Which synthesis is more complex: CIAC-105 or CIAC-106?",
            "Compare the building units of different MOPs"
        ]
    },
    {
        "category": "Corpus Statistics",
        "questions": [
            "How many MOPs are in the knowledge graph?",
            "What are the most commonly used chemicals in MOPs synthesis?",
            "What types of synthesis steps are most frequent?",
            "Give me statistics about the knowledge graph",
            "Tell me what do you know about this knowledge graph, what is this KG about?",
            "What is the most commonly used organic precursor used in sythensis of all mops?"
        ]
    }
]

@app.route('/')
def index():
    """Render the main chat interface."""
    # Generate a unique session ID for this user
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    
    return render_template('index.html', 
                         examples=EXAMPLE_QUESTIONS,
                         session_id=session['session_id'])

@app.route('/api/ask', methods=['POST'])
def ask_question():
    """
    Handle question from the user.
    
    Request JSON:
        {
            "question": "What is the recipe for VMOP-17?",
            "session_id": "uuid"
        }
    
    Response JSON:
        {
            "answer": "...",
            "metadata": {
                "tokens": 1234,
                "cost": 0.005,
                "timestamp": "2024-..."
            },
            "status": "success"
        }
    """
    try:
        data = request.get_json()
        question = data.get('question', '').strip()
        session_id = data.get('session_id', session.get('session_id', 'unknown'))
        
        if not question:
            return jsonify({
                "status": "error",
                "error": "Question cannot be empty"
            }), 400
        
        logger.info(f"[{session_id}] Received question: {question[:100]}...")

        # Fetch previous turn memory (one-turn)
        with _last_turn_lock:
            prev = _last_turn_by_session.get(session_id, {})
            prev_q = prev.get("question")
            prev_a = prev.get("answer")
        
        async def _answer(q: str):
            # Create everything inside the async thread/loop
            marie = MarieAgent(model_name="gpt-4o-mini")
            return await marie.ask(q, previous_question=prev_q, previous_answer=prev_a)

        # Run async query on the shared background event loop
        answer, metadata = run_async(_answer(question), timeout=600)

        # Update last-turn memory only on success
        with _last_turn_lock:
            _last_turn_by_session[session_id] = {"question": question, "answer": answer}
        
        # Format response
        response = {
            "status": "success",
            "answer": answer,
            "metadata": {
                "tokens": metadata['aggregated_usage']['total_tokens'],
                "cost": metadata['aggregated_usage']['total_cost_usd'],
                "calls": metadata['aggregated_usage']['calls'],
                "timestamp": datetime.now().isoformat()
            }
        }
        
        logger.info(f"[{session_id}] Answer generated (tokens: {response['metadata']['tokens']})")
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Error processing question: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    try:
        # Service is healthy if we can reach this endpoint
        # Each question creates its own agent instance
        return jsonify({
            "status": "healthy",
            "service": "MOP Extraction Agent - Web Extension",
            "agent": "instance per request"
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500

@app.route('/api/examples', methods=['GET'])
def get_examples():
    """Get example questions."""
    return jsonify({
        "status": "success",
        "examples": EXAMPLE_QUESTIONS
    })

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {error}", exc_info=True)
    return render_template('500.html'), 500

if __name__ == '__main__':
    import argparse
    import atexit
    
    # Register cleanup handler
    def cleanup():
        # Stop background asyncio loop thread
        global _async_loop, _async_thread
        if _async_loop is not None:
            logger.info("Stopping background asyncio loop...")
            try:
                _async_loop.call_soon_threadsafe(_async_loop.stop)
            except Exception:
                pass
        if _async_thread is not None:
            try:
                _async_thread.join(timeout=2)
            except Exception:
                pass
        if _async_loop is not None:
            try:
                _async_loop.close()
            except Exception:
                pass
    
    atexit.register(cleanup)
    
    parser = argparse.ArgumentParser(description="Run MOP Extraction Agent web extension")
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    parser.add_argument('--port', type=int, default=5000, help='Port to bind to')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    args = parser.parse_args()
    
    print("="*80)
    print("MOP Extraction Agent - Web Extension")
    print("="*80)
    print(f"\n🚀 Starting web server...")
    print(f"📍 URL: http://{args.host}:{args.port}")
    print(f"🔍 Debug mode: {'ON' if args.debug else 'OFF'}")
    print("\nPress Ctrl+C to stop the server\n")
    print("="*80)
    
    try:
        app.run(host=args.host, port=args.port, debug=args.debug)
    finally:
        cleanup()

