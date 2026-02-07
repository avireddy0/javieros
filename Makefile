.PHONY: test test-webui-smoke

test: test-webui-smoke

test-webui-smoke:
	./tests/webui-smoke.sh
