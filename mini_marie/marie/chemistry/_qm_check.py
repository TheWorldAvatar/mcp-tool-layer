import sqlite3
from mini_marie.marie.chemistry.chemistry_cache import db_path

c = sqlite3.connect(db_path())
print("types", c.execute("SELECT result_type, COUNT(*) FROM corpus_qm_results GROUP BY result_type").fetchall())
print("species", c.execute("SELECT DISTINCT species_fragment FROM corpus_qm_results").fetchall())
print("basis", c.execute("SELECT DISTINCT basis_fragment FROM corpus_qm_results WHERE basis_fragment != ''").fetchall())
print("zpe sample", c.execute("SELECT species_fragment, basis_fragment, value FROM corpus_qm_results WHERE result_type='ZeroPointEnergy' LIMIT 5").fetchall())
