# /doc — project documentation generator

Self-contained. Generates `Legilimens.docx` (mind map + architecture + a diagram for each
proxy step). Nothing here touches the main app.

## Regenerate
```powershell
# install the doc-only deps once (into the existing .venv is fine)
.venv\Scripts\python.exe -m pip install -r doc\requirements.txt

# build the document
.venv\Scripts\python.exe doc\generate_doc.py
```

Output: `doc/Legilimens.docx` (+ `doc/assets/*.png`). Both are gitignored — edit
`doc/generate_doc.py` and re-run to change them.
