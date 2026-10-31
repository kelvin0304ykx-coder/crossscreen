import sys

if "--engine-worker" in sys.argv:
    from crossscreen.worker import worker_main
    raise SystemExit(worker_main(sys.argv[sys.argv.index("--engine-worker") + 1:]))

from crossscreen.app import main
raise SystemExit(main())
