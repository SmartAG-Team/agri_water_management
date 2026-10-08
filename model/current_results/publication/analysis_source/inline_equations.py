"""Editable inline Office Math and matching MathML reading-copy rendering."""
from functools import lru_cache
from pathlib import Path
import hashlib
import re
import zipfile

from lxml import etree, html
from latex2mathml.converter import convert as to_mathml
from mathml2omml import convert as to_omml

M='http://schemas.openxmlformats.org/officeDocument/2006/math'
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS={'m':M,'w':W}

# Each left-hand string is the unchanged expression in the reviewed manuscript.
EQUATIONS={
 'f_c = 1−exp(−k_L LAI)':r'f_c=1-\exp(-k_L\mathrm{LAI})',
 'ΔB = 10 R_s p_PAR f_c ε f_T f_W^γ f_N':r'\Delta B=10R_s p_{\mathrm{PAR}} f_c\varepsilon f_T f_W^{\gamma}f_N',
 'p_PAR = 0.45':r'p_{\mathrm{PAR}}=0.45',
 'h(DVS)':r'h(\mathrm{DVS})',
 'exp[−h(DVS) max(T_mean−T_base,0)](1−q_s)':r'\exp\left[-h(\mathrm{DVS})\max(T_{\mathrm{mean}}-T_{\mathrm{base}},0)\right](1-q_s)',
 'q_s = 0.025(1−f_W f_N)':r'q_s=0.025(1-f_W f_N)',
 'N_g = 0.1 B_f c_g':r'N_g=0.1B_f c_g',
 'Q_g = 10 N_g m_g':r'Q_g=10N_g m_g',
 'T_p = K_T ET₀ f_c':r'T_p=K_T\mathrm{ET}_0 f_c',
 'E_p = K_E ET₀(1−f_c)':r'E_p=K_E\mathrm{ET}_0(1-f_c)',
 'T_a + E_s + E_c':r'T_a+E_s+E_c',
 'TEW = (θ_FC−θ_AD) Δz₁':r'\mathrm{TEW}=(\theta_{\mathrm{FC}}-\theta_{\mathrm{AD}})\Delta z_1',
 'REW = 0.30 TEW':r'\mathrm{REW}=0.30\mathrm{TEW}',
 'D_e = (θ_FC−θ₁) Δz₁':r'D_e=(\theta_{\mathrm{FC}}-\theta_1)\Delta z_1',
 'K_r = min[1,max(0,(TEW−D_e)/(TEW−REW))]':r'K_r=\min\left[1,\max\left(0,\frac{\mathrm{TEW}-D_e}{\mathrm{TEW}-\mathrm{REW}}\right)\right]',
 's_i = min[1,max(0,W_i−θ_WP,i Δz_i)/((1−p)(θ_FC,i−θ_WP,i) Δz_i)]':r's_i=\min\left[1,\frac{\max(0,W_i-\theta_{\mathrm{WP},i}\Delta z_i)}{(1-p)(\theta_{\mathrm{FC},i}-\theta_{\mathrm{WP},i})\Delta z_i}\right]',
 'S_e = (θ−θ_AD)/(θ_SAT−θ_AD)':r'S_e=\frac{\theta-\theta_{\mathrm{AD}}}{\theta_{\mathrm{SAT}}-\theta_{\mathrm{AD}}}',
 'm = 1−1/n':r'm=1-\frac{1}{n}',
 'S_e = [1+(α|h|)^n]^−m':r'S_e=\left[1+(\alpha|h|)^n\right]^{-m}',
 'K = K_sat S_e^(1/2)[1−(1−S_e^(1/m))^m]^2':r'K=K_{\mathrm{sat}}S_e^{1/2}\left[1-\left(1-S_e^{1/m}\right)^m\right]^2',
 'K_sat,face = d/[Δz_i/(2K_sat,i)+Δz_(i+1)/(2K_sat,i+1)]':r'K_{\mathrm{sat,face}}=\frac{d}{\frac{\Delta z_i}{2K_{\mathrm{sat},i}}+\frac{\Delta z_{i+1}}{2K_{\mathrm{sat},i+1}}}',
 'd = (Δz_i+Δz_(i+1))/2':r'd=\frac{\Delta z_i+\Delta z_{i+1}}{2}',
 'r_face = [Δz_(i+1) r_i + Δz_i r_(i+1)]/[Δz_i+Δz_(i+1)]':r'r_{\mathrm{face}}=\frac{\Delta z_{i+1}r_i+\Delta z_i r_{i+1}}{\Delta z_i+\Delta z_{i+1}}',
 'r_i = K_i/K_sat,i':r'r_i=\frac{K_i}{K_{\mathrm{sat},i}}',
 'K_face = K_sat,face r_face':r'K_{\mathrm{face}}=K_{\mathrm{sat,face}}r_{\mathrm{face}}',
 'q_i = K_face[1+(h_i−h_(i+1))/d]':r'q_i=K_{\mathrm{face}}\left[1+\frac{h_i-h_{i+1}}{d}\right]',
 'ΔS = P + I − ET_a − D − Q':r'\Delta S=P+I-\mathrm{ET}_a-D-Q',
 '1.724 × SOC/100':r'\frac{1.724\times\mathrm{SOC}}{100}',
 '1−BD/2.65':r'1-\frac{\mathrm{BD}}{2.65}',
 'ΣⱼAⱼΣₖxⱼₖȲⱼₖ':r'\sum_j A_j\sum_k x_{jk}\bar{Y}_{jk}',
 'Σₖxⱼₖ=1':r'\sum_k x_{jk}=1',
 '0≤xⱼₖ≤1':r'0\le x_{jk}\le1',
 'ΣⱼAⱼΣₖxⱼₖIₖ=(1−r)ΣⱼAⱼI₁':r'\sum_j A_j\sum_k x_{jk}I_k=(1-r)\sum_j A_j I_1',
 'r=0.25':r'r=0.25',
}
VARIABLES={
 'f_c':r'f_c','k_L':r'k_L','R_s':r'R_s','p_PAR':r'p_{\mathrm{PAR}}',
 'f_T':r'f_T','f_W':r'f_W','f_N':r'f_N','q_s':r'q_s',
 'T_mean':r'T_{\mathrm{mean}}','T_base':r'T_{\mathrm{base}}',
 'N_g':r'N_g','B_f':r'B_f','c_g':r'c_g','Q_g':r'Q_g','m_g':r'm_g',
 'T_p':r'T_p','K_T':r'K_T','E_p':r'E_p','K_E':r'K_E',
 'T_a':r'T_a','E_s':r'E_s','E_c':r'E_c','ET_a':r'\mathrm{ET}_a',
 'θ_FC':r'\theta_{\mathrm{FC}}','θ_AD':r'\theta_{\mathrm{AD}}',
 'θ_SAT':r'\theta_{\mathrm{SAT}}','θ₁':r'\theta_1','Δz₁':r'\Delta z_1',
 'D_e':r'D_e','K_r':r'K_r','s_i':r's_i','W_i':r'W_i',
 'Δz_i':r'\Delta z_i','θ_WP,i':r'\theta_{\mathrm{WP},i}',
 'θ_FC,i':r'\theta_{\mathrm{FC},i}','S_e':r'S_e',
 'K_sat':r'K_{\mathrm{sat}}','K_sat,face':r'K_{\mathrm{sat,face}}',
 'K_sat,i':r'K_{\mathrm{sat},i}','K_i':r'K_i','r_i':r'r_i',
 'r_face':r'r_{\mathrm{face}}','K_face':r'K_{\mathrm{face}}',
 'q_i':r'q_i','h_i':r'h_i','ET₀':r'\mathrm{ET}_0',
 'xⱼₖ':r'x_{jk}','Ȳⱼₖ':r'\bar{Y}_{jk}',
}
CATALOG=EQUATIONS|VARIABLES
PATTERN=re.compile(r'(?<!\w)('+ '|'.join(re.escape(k) for k in sorted(CATALOG,key=len,reverse=True))+r')(?!\w)')


def occurrences(text):
    return list(PATTERN.finditer(text))


@lru_cache(None)
def mathml(source):
    return to_mathml(CATALOG[source],display='inline')


@lru_cache(None)
def omml_xml(source):
    value=to_omml(mathml(source)).replace('<m:oMath>',f'<m:oMath xmlns:m="{M}" xmlns:w="{W}">',1)
    # mathml2omml 0.0.2 emits the wrong closing tag for overbar properties.
    value=value.replace('</m:groupChr><m:e>','</m:groupChrPr><m:e>')
    root=etree.fromstring(value)
    for run in root.xpath('.//m:r',namespaces=NS):
        properties=etree.SubElement(run,'{'+W+'}rPr')
        fonts=etree.SubElement(properties,'{'+W+'}rFonts')
        fonts.set('{'+W+'}ascii','Cambria Math');fonts.set('{'+W+'}hAnsi','Cambria Math')
        size=etree.SubElement(properties,'{'+W+'}sz');size.set('{'+W+'}val','22')
        run.insert(1,properties)
    return etree.tostring(root)


def add_text(paragraph,text):
    start=0
    for match in occurrences(text):
        paragraph.add_run(text[start:match.start()])
        paragraph._p.append(etree.fromstring(omml_xml(match.group())))
        start=match.end()
    paragraph.add_run(text[start:])


def signature(node):
    def row(element):
        return (element.tag,tuple(sorted(element.attrib.items())),element.text or '',tuple(row(c) for c in element))
    return hashlib.sha256(repr(row(node)).encode()).hexdigest()


def inject_reading_copy_math(tree,document):
    """Restore Office Math omitted by macOS Quick Look, using the same expressions."""
    lookup={signature(etree.fromstring(omml_xml(source))):source for source in CATALOG}
    with zipfile.ZipFile(document) as z:
        xml=etree.fromstring(z.read('word/document.xml'))
    candidates={}
    for paragraph in tree.xpath('//p'):
        candidates.setdefault(paragraph.text_content(),[]).append(paragraph)
    count=0
    for paragraph in xml.xpath('//w:p[m:oMath]',namespaces=NS):
        plain=''.join(paragraph.xpath('.//w:t/text()',namespaces=NS))
        matches=candidates.get(plain,[])
        if not matches:raise ValueError('Equation paragraph absent from reading copy: '+plain[:90])
        target=matches.pop(0)
        for child in list(target):target.remove(child)
        target.text=None
        for child in paragraph:
            if child.tag=='{'+M+'}oMath':
                source=lookup[signature(child)]
                node=html.fromstring(mathml(source))
                node.set('data-equation',source)
                target.append(node);count+=1
            else:
                text=''.join(child.xpath('.//w:t/text()',namespaces=NS))
                if text:
                    span=etree.Element('span');span.text=text;target.append(span)
    expected=len(xml.xpath('//m:oMath',namespaces=NS))
    assert count==expected,(document,count,expected)
    return count
