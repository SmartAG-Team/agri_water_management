"""Publication workflow for the selected calibration and consistent regional run."""
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'publication/figures/closed_axes'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'pdf.fonttype': 42, 'svg.fonttype': 'none'})
    fig, ax = plt.subplots(figsize=(7.5, 6.6))
    fig.subplots_adjust(top=.99, bottom=.015, left=.025, right=.975)
    ax.set(xlim=(0, 1), ylim=(0, 1)); ax.axis('off')

    def box(x, y, w, h, title, detail, outline='#0072B2'):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=.012,rounding_size=.025',
                                   edgecolor=outline, facecolor='#F3F7F9', linewidth=1.2))
        ax.text(x + w/2, y + h*.80, title, ha='center', va='center', fontsize=12,
                fontweight='bold', color='black')
        ax.text(x + w/2, y + h*.32, detail, ha='center', va='center', fontsize=10.6,
                linespacing=1.3, color='black')

    def arrow(start, end):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle='-|>', mutation_scale=13,
                                    color='#50565C', linewidth=1.2))

    box(.04, .85, .43, .13, 'Environmental inputs', 'Daily weather and soil profiles\nMapped wheat–maize rotation area')
    box(.53, .85, .43, .13, 'Multisite growth observations', '4 wheat sites and 5 maize sites\nWhole site-years; inherited growth fit')
    box(.04, .665, .43, .125, 'Retained growth parameters', 'Fixed growth coefficients\nConditional station transfer', '#009E73')
    box(.53, .665, .43, .125, 'Field water calibration', 'Wuqiao: fit 2016–2018\nRetrospective evaluation: 2019', '#D55E00')
    arrow((.26, .835), (.26, .805)); arrow((.72, .835), (.36, .805))
    arrow((.49, .727), (.515, .727))
    box(.04, .45, .92, .145, 'Continuous regional simulations',
        'One frozen parameter set; 32 units × 5 irrigation levels\nWheat–maize rotations and fallows, 1997–2025')
    arrow((.74, .65), (.74, .61))
    box(.04, .235, .43, .15, 'Spatial irrigation allocation',
        'Uniform and optimized reductions\nCommon regional water budgets\nSelection: 1997–2013')
    box(.53, .235, .43, .15, 'Rainfall–storage strategies',
        'GRACE storage + antecedent rainfall\nClass strategies and rainfall controls\nSelection: 2003–2013')
    arrow((.26, .435), (.26, .40)); arrow((.74, .435), (.74, .40))
    box(.04, .035, .92, .13, 'Regional comparisons: 2014–2025',
        'Grain production · field irrigation · ET · drainage\nRetrospective conditional management scenarios', '#D55E00')
    arrow((.26, .22), (.26, .18)); arrow((.74, .22), (.74, .18))
    for extension in ['png', 'pdf', 'svg']:
        fig.savefig(OUT / f'Figure_2_model_and_experiment.{extension}', dpi=600)
    plt.close(fig)


if __name__ == '__main__':
    main()
