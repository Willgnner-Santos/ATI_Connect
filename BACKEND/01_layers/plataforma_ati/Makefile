# Invoked by SAM inside the Python 3.14 / Linux x86_64 build container.
.PHONY: build-PlataformaAtiLayerLocal
build-PlataformaAtiLayerLocal:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	cp -R python/. "$(ARTIFACTS_DIR)/python/"
	python3.14 -m pip install --no-cache-dir -r requirements.txt -t "$(ARTIFACTS_DIR)/python"
