SHELL := /bin/bash

.PHONY: crawl index build run clean

crawl:
	@bash scripts/crawl_cloudscape.sh

typedoc:
	@bash scripts/generate_typedoc.sh

index:
	@python3 scripts/build_index_bm25.py \
	  --wacz data/wacz/collections/cloudscape/cloudscape.wacz \
	  --typedoc data/typedoc_md \
	  --verbose \
	  --db build/index.db

build:
	@docker build -t cloudscape-rag-bm25 .

run:
	@docker run --rm -p 8000:8000 cloudscape-rag-bm25

clean:
	rm -rf build data/wacz
