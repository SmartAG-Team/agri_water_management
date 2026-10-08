"""Independent metric definitions, separate from calibration helpers."""
import json
import numpy as np
import pandas as pd

def close(a,b,label):
    assert np.allclose(a,b,rtol=0,atol=1e-8,equal_nan=True),label


def same_json(a,b):
    # Preserve matching missing cultivar metadata without treating finite
    # weather or hydraulic values as missing. Their numeric checks remain below.
    return json.dumps(a,sort_keys=True)==json.dumps(b,sort_keys=True)


def weights(f):
    w=pd.Series(0.,index=f.index)
    for _,v in f.groupby('variable'):
        for _,s in v.groupby('site'):
            for _,y in s.groupby('source_group_id'):
                for _,c in y.groupby('case_id'):
                    w.loc[c.index]=1./(f.variable.nunique()*v.site.nunique()*s.source_group_id.nunique()*y.case_id.nunique()*len(c))
    close(w.sum(),1.,'hierarchical weight sum')
    return w.to_numpy()


def moments(o,p,w=None):
    o,p=np.asarray(o,float),np.asarray(p,float)
    if w is None:w=np.full(len(o),1./len(o))
    mean=float(w@o);pmean=float(w@p);mse=float(w@(p-o)**2)
    variance=float(w@(o-mean)**2);pvar=float(w@(p-pmean)**2)
    return dict(rmse=mse**.5,bias=float(w@(p-o)),
        nrmse_percent=100*mse**.5/mean if abs(mean)>1e-12 else np.nan,
        nse=1-mse/variance if variance>1e-12 else np.nan,
        r_squared=float(w@((o-mean)*(p-pmean)))**2/(variance*pvar) if variance*pvar>1e-12 else np.nan)

