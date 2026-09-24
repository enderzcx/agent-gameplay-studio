# Offline-first entry points. Nothing here needs network, credentials, a game, or a display.
# `make check` is the one command to run before trusting this repository.

PY ?= python3
C  := skills/gameplay-postproduction/scripts/check_postproduction.py
A  := skills/gameplay-postproduction/scripts/check_timeline_audit.py

.DEFAULT_GOAL := help
.PHONY: help check test-checker test-audit test-assembly test-tts recorder recorder-offline check-examples clean

help:  ## Show this help
	@echo "agent-gameplay-studio — available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Full offline verification:  make check"
	@echo "Structure-only examples:    make check-examples"

check: test-checker test-audit test-assembly test-tts  ## Run every offline test (no network, no keys, no game)
	@echo
	@echo "offline tests: OK"

test-checker:  ## Deterministic checker: structure modes, ready gate, and negative cases
	$(PY) tests/test_check_postproduction.py

test-audit:  ## Input preflight + adopted-timeline audit on anonymous synthetic fixtures
	$(PY) tests/test_timeline_audit.py

test-assembly:  ## EDL assembler smoke test on synthetic media (real ffmpeg)
	$(PY) tests/test_build_sample.py

test-tts:  ## TTS hard guarantees against a local fake endpoint (loopback only)
	$(PY) tests/test_tts_guarantees.py

check-examples:  ## Run the checker over examples/ (structure semantics only)
	@for m in timeline units sheet; do \
		case $$m in timeline) f=examples/timeline.example.tsv;; \
		             units)    f=examples/units.example.tsv;; \
		             sheet)    f=examples/review-sheet.example.md;; esac; \
		$(PY) "$(C)" $$m "$$f" || exit 1; \
	done
	@echo "structure checks: all examples valid (this does NOT mean a cut is good)"

recorder:  ## Build the macOS recorder (tools/gamerec/build/GameAVRec.app)
	tools/gamerec/build.sh

recorder-offline: recorder  ## Build the recorder, then run its permission-free checks
	bash tools/gamerec/tests/regression.sh

clean:  ## Remove build output and caches
	rm -rf tools/gamerec/build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "cleaned"
