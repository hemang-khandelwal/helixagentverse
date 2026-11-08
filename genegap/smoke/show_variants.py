"""Run task 2.1 for real:  python smoke/show_variants.py CYP2C19
Prints the filtered variant table. Needs internet and your .env."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genegap.clients.clinvar import ClinVarClient
from genegap.clients.gnomad import GnomadClient
from genegap.store import Store
from genegap.variants import format_table, variants_for_gene

if __name__ == "__main__":
    gene = sys.argv[1] if len(sys.argv) > 1 else "CYP2C19"
    store = Store()
    print(format_table(variants_for_gene(gene, GnomadClient(store=store), ClinVarClient(store=store))))



#testing: python scripts/m1_clopidogrel.py --gene CYP2C19 --drug clopidogrel