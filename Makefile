DESTDIR ?= /
PREFIX ?= /usr
PYTHON ?= python3

install:
	# Cleanup temporary files
	rm -f INSTALLED_FILES

	# Use Python setuptools
	${PYTHON} ./setup.py install -O1 --prefix="${PREFIX}" --root="${DESTDIR}" --record=INSTALLED_FILES

test:
	${PYTHON} -m pytest -svvv tests --junitxml=junit.xml

coverage:
	${PYTHON} -m pytest -svvv --cov=tapest_client --cov-fail-under=80 --cov-report=term-missing tests
	coverage xml

clean: clean-rpm
	find . -iname '*.pyc' -type f -delete
	find . -iname '__pycache__' -exec rm -rf '{}' \; | true
	rm -rf coverage.xml htmlcov junit.xml .coverage

clean-rpm:
	rm -rf rpmbuild
