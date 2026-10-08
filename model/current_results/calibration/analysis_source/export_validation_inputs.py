"""Single workbook of source observations and exact unchanged scenario inputs."""
from pathlib import Path
from io import StringIO
import hashlib
import json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]


def main():
    if (ROOT/'verification/artifact_manifest.json').exists():raise RuntimeError('Sealed run preserved.')
    path=ROOT/'verification/input_manifest.json';manifest=json.loads(path.read_text())
    target=Path(__file__).resolve().relative_to(ROOT).as_posix()
    if target not in {row['snapshot'] for row in manifest}:
        manifest.append(dict(source=str(Path(__file__).resolve()),snapshot=target,
            sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    for row in manifest:
        assert hashlib.sha256((ROOT/row['snapshot']).read_bytes()).hexdigest()==row['sha256'],row['snapshot']
    path.write_text(json.dumps(manifest,indent=2)+'\n')
    weather,soil,initial,events=[],[],[],[]
    for path in sorted((ROOT/'inputs/reference').rglob('*.json')):
        p=json.loads(path.read_text());kind=path.parent.name
        case=path.name.removesuffix('_crop.json') if kind=='field' else path.stem
        segments=[('crop',p['inputs'])]+([('presowing',p['presowing']['inputs'])] if p.get('presowing') else [])
        for segment,inputs in segments:
            tags=dict(case_id=case,kind=kind,segment=segment,latitude_deg=inputs['latitude_deg'])
            weather.extend(dict(row,**tags) for row in inputs['weather'])
            soil.extend(dict(row,layer=i,**tags) for i,row in enumerate(inputs['soil_layers']))
            events.extend(dict(row,**tags) for row in inputs.get('irrigation_events',[]))
            role='declared_but_overridden_by_presowing_state' if segment=='crop' and p.get('presowing') else 'supplied_initialization'
            initial.extend(dict(layer=i,theta=value,role=role,**tags) for i,value in enumerate(inputs['initial_theta']))
    frames={}
    obs=pd.read_csv(ROOT/'data/observations.csv',low_memory=False)
    for variable,g in obs.groupby('variable'):frames['Observed_'+variable]=g
    for name,file in [('Confirmed_field_biomass','confirmed_field_biomass.csv'),
                      ('Raw_field_water','wuqiao_used_seasonal_observations.csv'),
                      ('Digitized_field_yield','wuqiao_digitized_annual_yields.csv'),
                      ('Case_inventory','case_inventory.csv'),('Whole_year_partitions','partitions.csv')]:
        frames[name]=pd.read_csv(ROOT/'data'/file,low_memory=False)
    for name,rows in [('Exact_weather',weather),('Exact_soil_layers',soil),('Declared_initial_profiles',initial),('Exact_irrigation',events)]:
        frame=pd.DataFrame(rows);frames[name]=frame
        destination=ROOT/'data'/(name.lower()+('.csv.gz' if name=='Exact_weather' else '.csv'))
        if destination.exists():
            # Retain exact archived bytes, including gzip metadata, after checking
            # that the export reconstructed from unchanged inputs is equivalent.
            retained=pd.read_csv(destination,low_memory=False)
            # Hourly arrays are lists in model inputs and serialized text in CSV.
            serialized=pd.read_csv(StringIO(frame.to_csv(index=False)),low_memory=False)
            pd.testing.assert_frame_equal(serialized,retained,check_dtype=False,
                check_exact=False,rtol=1e-12,atol=1e-12)
        else:
            frame.to_csv(destination,index=False)
    for name,file in [('Biomass_measurement_basis','data/biomass_eligibility.json'),
                      ('Calibration_protocol','verification/control_protocol.json')]:
        values=json.loads((ROOT/file).read_text())
        frames[name]=pd.DataFrame([dict(property=k,value=json.dumps(v,ensure_ascii=False)) for k,v in values.items()])
    frames['Source_SHA256']=pd.DataFrame(manifest)
    frames['Run_status']=pd.DataFrame([dict(property='calibration',value='in progress'),
        dict(property='new_testing_results',value='pending'),dict(property='independent_validation',value=False)])
    with pd.ExcelWriter(ROOT/'tables/validation_data_and_results.xlsx',engine='openpyxl') as writer:
        for name,frame in frames.items():
            frame.to_excel(writer,sheet_name=name,index=False)
            worksheet=writer.sheets[name];worksheet.freeze_panes='A2';worksheet.auto_filter.ref=worksheet.dimensions
    print('Exported',len(frames),'input sheets;',len(weather),'exact weather rows;',len(initial),'declared soil-water layers.')


if __name__=='__main__':main()
