"""Web UI for unredact.tools (pip install "unredact-tools[web]")."""
import os

# numpy/OpenBLAS, OpenCV and tesseract each default to one thread per core. A web worker
# already runs a bounded number of jobs (UNREDACT_WORKERS), and on a shared host those
# per-core pools exhaust the account's process limit and crash workers. Set before any
# of them is imported; tesseract inherits it as a subprocess.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_THREAD_LIMIT"):
    os.environ.setdefault(_var, "1")
