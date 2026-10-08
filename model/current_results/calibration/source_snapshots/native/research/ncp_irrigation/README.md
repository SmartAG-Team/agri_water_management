# 华北平原作物—灌溉科研模型

　　研究分支：`research/ncp-irrigation-model`。独立开发目录：`open_crop_model/.worktrees/ncp-irrigation`。环境：Python 3.12。生产接口和现有作物案例的默认行为保持原有版本。

　　模型提供分层守恒水量、湿润区—非湿润区双域、四种地表施水边界、参考ET、现有作物过程耦合、连续小麦—玉米轮作及区域地下水和费用核算。当前参数属于工程先验，田间性能与技术排序尚未通过独立观测验证。

## 运行

```bash
cd /Users/gangzhao/Documents/workspace/open_crop_model/.worktrees/ncp-irrigation
.venv/bin/python -m pytest -o python_files='test_*.py' tests/hydrology tests/research/ncp_irrigation -q
.venv/bin/python -m research.ncp_irrigation.run_validation \
  --config research/ncp_irrigation/configs/validation.json --run-id field-v3
```

　　每次运行使用新的`run-id`，已有结果不会覆盖。资料目录由配置中的`data_root`指定。结果位于`research/ncp_irrigation/results/<run-id>/`，派生资料和结果均不进入Git；原始数据只读。返回码1表示至少一项阶段验收未通过，证据不足保留在验收表中。

## 过程与边界

| 模块 | 实现内容 | 适用限制 |
| --- | --- | --- |
| 水分核心 | 绝对储水、饱和容量、积水、径流、导水限制、日内子步和逐项账本 | 守恒分层桶模型，不能解释二维湿润锋 |
| 双域 | 局部施水、全面降雨、守恒域间交换、交接时面积重映射 | 一段连续试验内几何固定；湿润区假定为嵌套面积 |
| 四方式 | `flood`、`surface_drip`、`micro_sprinkler`、`sprinkler`；截留、施水蒸发和风漂显式输入 | 名称不预置效率或产量排序；仅支持地表施水 |
| ET0 | FAO-56 Penman–Monteith、显式Hargreaves或给定逐日参考需求`provided` | 给定需求须为非负有限mm/day；无静默气象补齐 |
| 蒸发和吸水 | 实际表层水分控制干燥，蒸发受空气干燥下限约束，根系吸水受萎蔫下限约束 | 无额外虚拟蒸发水库；实际蒸腾与潜在需求之比作用于生长一次 |
| 作物 | 复用OCM品种参数、分配、叶面积、生物量和籽粒过程；小麦春化与光周期、玉米熟期组 | 参数跨技术共享；养分采用独立固定情景因子，没有完整氮循环 |
| 轮作 | 土壤、积水和冠层水跨季传递；休闲段处理气象与降雨 | 休闲日必须有显式天气；无每季实测水分重置，无残茬过程 |
| 底部 | 自由排水，或独立给定水位下的平衡含水量与导水交换 | 给定水位是简化桶模型边界；浅水位外推需要独立验证 |
| 区域 | 气候—土壤分区批量连续运行和面积加权 | 政策模式要求V0、V1通过并限定已验证灌溉方式；测试模式明确标注未验证 |
| 地下水 | 供水来源、输水损失、毛管取水、补给滞后及期末待补给水 | 根区底部排水不等于同日含水层补给；不识别历史政策因果贡献 |
| 经济 | 投资年化、维护、人工、扬程与设备压力耗能、供水价格、盈亏平衡投资 | 价格和设备参数须独立提供；含电费的供水价格禁止再次收取电费 |

　　田块水量单位为mm，域内水量为各自局部面积上的mm，区域体积为m³。面积换算为`1 mm × 1 ha = 10 m³`。输水损失位于田块边界之外；土壤排水、施水蒸发和输水损失分开记账。

　　参考ET公式和单位转换依据[FAO-56气象资料](https://www.fao.org/4/x0490e/x0490e07.htm)及[参考ET计算](https://www.fao.org/4/x0490e/x0490e08.htm)。表层蒸发采用实际储水的干燥诊断，其简化响应尚未完成实测率定，不等同于完整双作物系数算法。

## 单季输入

　　`simulate_season(inputs, parameters, initial_state=None)`返回每日预测、季节汇总和末日状态。`inputs`含`crop`、起止日期、纬度、海拔、显式ET方法、连续逐日天气、土壤层、首段独立初始含水量、设备边界和已知灌溉事件。天气采用℃、mm、MJ m⁻² d⁻¹、m s⁻¹；土壤阈值采用m³ m⁻³，层厚采用mm。

　　每次灌溉具有唯一`event_id`、日期、等效水深及`measurement_location`。泵端计量需要明确`conveyance_fraction`；田块计量水量直接进入田块账本。持续时间缺测时使用带标记的24小时均匀施水假设。跨午夜事件必须显式拆分。

　　设备边界具有`method`、`wetted_fraction`、`application_evaporation_fraction`、`drift_fraction`、`canopy_fraction`和`application_depth_mm=0`。同边界、同天气和同事件的技术名称变更不会改变预测。设备边界与灌溉日历分开，水量和频次不会随技术名称暗中调整。

　　参数分别位于`hydrology`和`crop`。`crop.profile`中的辐射利用效率、初始LAI、根深和籽粒参数属于共享品种参数；`nutrition_factor`为独立固定情景因子。科研过程输入拒绝`observed_yield_kg_ha`和顶层`yield_target_kg_ha`；`management_target_yield_kg_ha`不参与过程计算。

　　物候率定卡通过`crop.wheat_phenology.stage_thresholds`（完整BBCH节点）或`crop.maize_phenology.stage_thermal_time_c`（完整Iowa节点）输入，节点须从零开始、严格递增且全部有限。无率定卡时使用原有默认节点；导入卡只改变该次模拟，不改变全局作物配置。

　　科研耦合模型的`crop.germination_water_response`默认采用`seed_layer_available_water`：播种层面积加权可提取水量大于零后开始累积热量与春化。`inputs.sowing_depth_mm`默认50 mm，用于定位模型土层；该层的平均水量不能代替种子尺度水势观测。当天降水与灌溉可使次日满足萌发条件。`temperature_only`保留仅按气温推进的对照。土壤水分约束萌发的原则见[APSIM Wheat 7.5 R3008技术文档第3.2节](https://www.apsim.info/wp-content/uploads/2019/09/WheatDocumentation.pdf)；默认深度和田间层均值近似属于独立先验，数值检验不构成田间出苗验证。

　　`inputs.tillage_events`接受具有唯一`event_id`、ISO日期`date`和`depth_mm`的耕作事件。操作在记录日期的水分更新与萌发判定之前，按耕深垂直混合已有土壤水分，保留耕深以下及相交层未耕部分的储水量；湿润域和非湿润域分别守恒。操作不增加水量、不改变土壤水力参数，也不模拟残茬分解或养分释放。无耕作事件的预测保持原有过程。耕作日期可来自田间记录，缺测耕深仅支持明确标记的敏感性分析。

　　2026年10月2日的独立年份率定结果位于农业水管理项目的`model/2026-10-02_phenology_calibration/`（AgERA5）和`model/2026-10-02_phenology_calibration_station/`（实测温度）。`parameters/<crop>_frozen.json`可由`load_frozen_parameters`加载并作为`simulate_season`的参数使用。两套卡对应不同气象驱动，不能作为同一参数集混用。小麦证据覆盖至抽穗，晚播越冬分蘖误差尚未解决；物候率定不构成叶面积、产量或水量过程验证。

　　五站点联合物候率定与验证结果位于农业水管理项目的`model/2026-10-02_phenology_all_sites/`。禹城、固城、栾城、封丘和商丘分别贡献较早年份作为校准、较晚年份作为验证，共32个校准站年和18个验证站年。事件拟合依次平衡站点、年份与样地权重，并另设留一站点检验；完整参数卡采用相同加载接口。共同有效预测上的站点等权验证MAE为小麦12.28天、玉米7.69天；封丘小麦分蘖及商丘玉米仍有较大偏差，固城晚播玉米尚有成熟阈值未达到的情况。参数卡属于条件校准结果，尚不支持区域应用验收。

　　物候性能改进结果位于`model/2026-10-02_phenology_improved/`，观测、气象与站年划分保持一致。校准集内部三折站年交叉验证选择小麦原有发育响应及绝对日期误差拟合、玉米八点正弦温度积分，并选择已知品种独立阈值卡及未见品种共享卡。共同有效预测上的站点等权验证MAE降至小麦9.29天、玉米4.73天，全部质量有效的验证阶段均有预测。单站年品种卡缺少同品种跨年校准检验，封丘小麦仍有明显偏差。

　　改进参数文件含`shared`和`cultivars`：先通过`load_frozen_parameters`加载，再按规范化品种名从`cultivars`选择参数卡，未见品种使用`shared`，将所选卡置于`simulate_season(inputs, {"crop": card})`。品种规范化规则见该次运行的`improve_phenology.py`，仅处理Unicode、空格及连接符。

　　`wheat_phenology.development_response="stage_specific"`是可选的早期热量响应结构，BBCH21前不施加光周期和春化折减；本次交叉验证保留`legacy`，阶段分离结构未进入选择后的预测。`maize_phenology.temperature_method`支持`daily_mean`与`subdaily`，`temperature_response`明确给出`base`、`optimal`和`maximum`，三温度须有限且严格递增。正弦温度积分不等于实测小时气温，率定的6°C基温不构成独立生理测定。

## 观测案例与冻结

　　晚期物候结果位于农业水管理项目的`model/2026-10-02_phenology_late_stages/`。四个冬小麦站点的92条有效蜡熟期记录采用条件性BBCH85参照，并单列BBCH83—87阶段定义范围；模型完熟终点与生长停止阈值为BBCH89，缺少独立完熟日期观测，收割日期属于管理终止边界。玉米R6与成熟前收割分别记录；未见品种继续使用共享卡。

　　`simulate_potential_season`调用现有作物生长核，水分胁迫系数固定为1，没有土壤水状态。养分默认不受限，显式`nutrition_factor`与`nutrition_by_management`可提供有效限制。条件性叶面积、生物量与有效作物系数ET结果位于`model/2026-10-02_growth_calibration_plot_verified/`，观测按来源样地匹配，已报告但与物候记录冲突的品种排除，封丘蒸渗仪日数据与时段水量平衡数据分别解析。辐射利用效率、比叶面积和初始叶面积属于有效参数。该路径不构成田间水量平衡、灌溉方式差异或地下水响应验收。

　　验证配置的`cases`包含`case_id`、`site_id`、`experiment_id`、`season_id`、`treatment_id`、`source_group_id`、`inputs_path`、`observations_path`、`split`、`management_complete=true`和`plot_alignment_confirmed=true`。`split`为`calibration`、`validation`或`excluded`。共享原始试验的论文、处理和重复使用同一`source_group_id`，不能跨校准与验证集。

　　观测表字段见[验证计划](../../docs/superpowers/plans/2026-10-02-ncp-irrigation-validation-plan.md)。有效观测必须包含五项案例标识、`replicate_id`、日期、变量、数值、单位、`qc_flag=pass`、`irrigation_method`和`spatial_support`；多处理共享文件按案例标识筛选，多重复使用显式`observation_aggregation=mean_replicates`。土壤水分支持`field_mean`、`wet_domain_mean`或`dry_domain_mean`，层厚按交叠匹配。点位向域均值的转换算子尚未验证，点观测直接评分时报错。时间匹配限于原观测日期，缺口不作最近日期填充。

　　`calibration_stages`按变量组配置完整候选参数与独立归一化尺度；验证案例不参与参数选择。冻结文件保存校准来源组和SHA256；导入时保留原始来源并拒绝与验证组重叠，导入冻结文件与重新率定不能同时配置。源数据缺少单位、完整管理或土壤水力参数时，相应样本保持排除。

　　验证划分按完整来源组及起始日期冻结，前60%用于校准，其余用于验证。至少6个完整作物季，两个集合均需覆盖小麦和玉米；调用者声明的划分必须与登记一致。V1要求逐季产量、分深度土壤水分、日ET、覆盖率不低于90%的季节ET、开花和成熟日期；土壤水分至少覆盖3个不同观测日，聚合置信区间至少包含2个独立来源组。零预测或未预测的物候事件不会被静默删除。`phenology_days`采用从模拟首日算起的日数，单位`days`，`phenology_event`为`anthesis`或`maturity`。

　　`contrasts`登记`case_a`、`case_b`、`variable`、`comparison`与`comparability_confirmed=true`；比较限定同试验、同季和同来源。变量为产量或季节ET，两个处理各有至少2个重复，重复重采样产生观测差异区间。`comparison`为`technology_package`或`same_field_water`，后者还需经核实的两处理田块净输入量。至少5个独立显著对照组及80%方向一致构成第一版技术差异门槛。

　　`rotations`登记`rotation_id`、连续`seasons`、管理和样地核实状态、至少2组`initial_theta_variants`及独立登记的初始化敏感性目标。每段`seasons`使用案例结构和观测文件；休闲天气显式提供。连续模拟与初始化扰动均保留跨季水分，V3评分使用相同观测算子和完整作物季指标。

　　`observed_uncertainty`引用`case_id`和显式`technologies`，提供至少200组`joint_draws`及分布来源。每组可包含共享`parameters`、天气／土壤／初始状态／灌溉日历的`shared_inputs`、设备扰动、`source`供水和补给边界、分技术`cost_by_method`、粮价、成本分摊比例及其他费用。共同驱动在技术间复用，输出抽水、补给、产量、ET和条件收益区间与排序概率。校准分布需记录且检查训练来源；独立先验不能称为后验。

　　`regional`支持分区驱动、参数映射、经验证的技术范围与共同面积／时间的农业通量检查。区域完整V4验收仍缺少独立冠层、产量空间资料和全土壤柱GRACE组分及地下水背景的对齐流程；单项农业通量达标不足以使V4通过。旱年目标覆盖计算函数已提供，完整区域旱年产品与200组轮作经济不确定性尚未进入默认运行配置。

　　冠层截留蒸发使用独立潜在需求，并减少同日蒸腾的能量需求；生长胁迫按扣除截留蒸发后的蒸腾需求计算。该简化竞争关系需要实测验证。公共水分入口检查日期连续性和日内重复事件。非零砾石剖面的旧接口转换需要独立细土容量定义，当前明确拒绝。作物档案的`profile.max_lai`不属于开放的覆盖参数；可选冠层版本通过`growth_process.max_lai`约束新叶分配。

　　叶面积与生物量改进结果位于农业水管理项目的`model/2026-10-02_growth_emergence_verified/`。45条原始小麦LAI超过研究阈值7，其中8条已有品种冲突，新增隔离37条；原始值完整保留，阈值不属于普遍生物学上限。五站点沿用原有完整站年划分，物候预先冻结。校准集三折站年交叉验证选择小麦`canopy_v3`热龄冠层和玉米`canopy_v2`容量冠层，均采用受收缩约束的站点有效RUE与SLA参数。玉米出苗判据采用VE阶段，VS阶段即使显示BBCH9仍保持零绿色叶面积与零同化。未知站点使用共同卡，区域迁移精度尚未检验。

　　可选版本限制出苗前的绿色冠层和同化，将超过叶面积容量的新干物质分配至非叶器官，衰老叶质量进入枯叶库。`canopy_v3`以面积加权平均热龄近似叶片更新，不能替代完整单叶队列。旧卡仍采用原有行为。RUE对应截获短波辐射，LAI为绿色叶面积，生物量包含绿色器官、枯叶、茎和籽粒。

```python
from research.ncp_irrigation.calibration import load_frozen_parameters
from research.ncp_irrigation.growth import resolve_growth_parameters
from research.ncp_irrigation.potential import simulate_potential_season

bundle = load_frozen_parameters(growth_parameter_path)
crop_card = dict(phenology_card)
crop_card.update(resolve_growth_parameters(bundle, site="Luancheng"))
daily = simulate_potential_season(inputs, crop_card)
# With independently documented soil/management, the same resolved card is
# accepted by simulate_season(inputs, {"crop": crop_card}).
```

　　相同筛选后留出观测的站点平衡MAE为：小麦LAI由1.346降至0.802 m²/m²，地上部干生物量由2.702降至2.078 t/ha；玉米LAI由1.064降至0.891 m²/m²，生物量由2.133降至2.086 t/ha。玉米生物量改善幅度较小，栾城部分指标恶化，异常仍计入评价。月内未知采样日以模拟月均值近似，当前有效ET和潜在生长结果仍不构成田间水量平衡或灌溉方式验证。

## 验证状态

　　校准与测试分别存放于农业水管理项目的`model/2026-10-02_growth_et_split/calibration/`和`testing/`，7629条观测及既有生长、物候系数保持不变。32页Excel与22组PNG/PDF仅显示校准模型，另含平衡及逐记录R²、MAE和RMSE。ET候选由校准站年三折交叉验证选择，测试年份仍属于此前重复检查的留出诊断。

　　`effective_et.simulate_effective_et`接收连续日天气、冻结冠层和ET系数。玉米R6不直接终止剩余绿叶蒸腾，零绿色叶面积对应零蒸腾；可选天气分配版本采用生育阶段和因果降水湿润代理。小麦与玉米测试ET MAE分别为1.580与2.329 mm/day，平衡R²分别为0.056与0.053。整体ET能力仍弱，小麦总体RMSE略有上升。该过程没有率定灌溉或根区水量平衡，封丘水量平衡残差与直接日观测分别评价；原始高值没有删除。[过程、指标与验证证据](../../docs/superpowers/specs/2026-10-02-effective-et-results.md)。

　　`field-v1`完成源文件校验、28,360条禹城观测的派生登记、36个作物季的适用性审计以及200组共享先验软件情景。全部36个作物季缺少已确认的管理、计量或土壤—样地配套输入，未进入观测率定。216条灌溉记录有计量与日历完整性问题，另有18条遗漏候选和5条夏玉米年份冲突。

　　V0结果来自软件物理案例；V1基础过程、V2四方式差异、V3连续轮作观测和V4区域适用性均保持证据不足。合成案例中的产量、ET、不确定性区间及技术差异不构成田间验证或灌溉推荐。基线、单域、双域、无交换和静态蒸发对照的独立观测收益尚未建立。

　　井网诊断按潜水与承压水分开，以埋深增加表示水位下降，完整公开井网时段为2005—2017。该诊断不能检验2018年后的全面恢复。GRACE核算要求共同日期、面积和基准期，以及全土壤柱、地表水、雪和冠层储水；根区储水不能代替全土壤柱。


　　最新ET过程检验位于农业水管理项目的`model/2026-10-02_et_energy_process_balanced/`，包含39页Excel、23组PNG/PDF及原始来源诊断。新增`energy_partition`和`energy_weather_partition`根据FAO-56辐射与空气动力分量确定驱动比例，保持输入参考ET0总量；纯能量分配中各响应系数为1时恢复参考ET0，包括低温小麦。`latitude_deg`和`elevation_m`须显式传入；当前实验的0 m共同参考高程不等同于实测站点高程。

　　`core.hydrology.evapotranspiration.reference_et0_components`支持实测日净辐射，保留有符号辐射分量和非负ET总量。默认拒绝日水汽压不一致；显式`zero_negative_daily_vpd`只在平均水汽压未超过日最高气温对应饱和阈值时允许将负日VPD的空气动力需求设为0，长波计算仍采用原水汽压，并输出质量标记。原始天气和观测保持不变。

　　候选选择采用全部校准折外预测的站点平衡RMSE，避免折内站点覆盖不一致导致的整体权重偏差。6291个最终ET时间窗和18960条交叉验证预测经过冻结参数重算，物候与生长预测保持原值。测试总体平衡ET RMSE为小麦2.840与玉米3.622 mm/day，平衡预测R²为0.082与0.070；增益较小，小麦直接日ET有所恶化。条件ET与实际水量平衡能力尚未达到研究目标。[结构、数据与检验证据](../../docs/superpowers/specs/2026-10-02-energy-partition-et-results.md)。

## 冠层队列与玉米叶片分配

　　`canopy_v4`为每批叶片保留绿色面积、干物质和正积温叶龄，逐队列计算衰老，新增叶片不会降低旧叶的死亡风险。面积加权叶龄仅作为输出诊断。叶片死亡对应的碳进入枯叶库，冠层容量限制后的剩余同化物进入非叶器官；潜在与水分耦合路径采用相同状态更新。出苗前种子储备不产生绿色叶面积。

　　玉米可设置`early_leaf_allocation_multiplier`与`late_leaf_allocation_multiplier`，V6前采用前者，V6至VT之间线性过渡，VT后采用后者；两系数的允许范围为0.1—3，最终叶片分配不超过可用营养生长量。小麦队列默认叶寿命为800 °C·day，显式`leaf_lifespan_c_days`可覆盖该值；玉米仅在显式设置叶寿命时应用热龄死亡风险。未启用新版本的参数卡保持原有行为。

　　`model/2026-10-02_canopy_cohorts/`中的玉米分配时序候选由校准站年合并CV选择，测试LAI RMSE为0.918 m²/m²、平衡预测R²为0.736。小麦队列没有改善校准CV，原参数保留。玉米生物量只有微小改善，直接日ET有所恶化；禹城缺少冻结测试年份的生物量测量。[过程、指标与覆盖证据](../../docs/superpowers/specs/2026-10-02-canopy-cohort-results.md)。

## 显式养分限制

　　作物参数`nutrition_factor`默认为1，`nutrition_by_management={"unfertilized": 0.75}`按输入`management_class="unfertilized"`提供独立处理系数。有效因子为标量与类别系数的乘积，同化物生产仅乘一次，原有衰老响应接受同一因子。空缺或未知类别保留标量假设，映射中的全部值均须有限且位于0—1，类别键为非空字符串。

　　改进冠层版本支持`growth_process.leaf_expansion_stress_policy="water_nutrition"`，叶片扩张接受实际水分及养分因子，容量约束后的剩余碳保留于非叶器官。默认`temperature_only`维持原卡的扩张响应；旧冠层版本拒绝新政策。条件性潜在路径仍没有土壤水状态，水分因子为1，养分受限时输出`conditional_growth_with_effective_nutrient_limitation_no_water_state`。该功能不包含土壤氮过程。

　　`model/2026-10-02_nutrient_canopy/`由校准CV选择小麦养分、叶扩张与叶龄队列结构，玉米保留上一轮结构。封丘不施肥小麦测试LAI RMSE降至0.211 m²/m²、生物量降至1.396 t/ha；全部小麦LAI略改善，生物量反而恶化。ET仍弱。栾城未用于本次拟合的EC补充检验中，小麦日ET RMSE为0.837 mm/day、预测R²为0.776，玉米为0.922及0.099，精确足迹与样地匹配未确认。[过程与完整限制](../../docs/superpowers/specs/2026-10-02-nutrient-canopy-results.md)。

### 实际ET观测与成熟冠层吸水

　　`et_observations.classify_et_targets`区分有明确方法的直接ET、未闭合水量残差及未确认观测方法；`require_direct_et`拒绝水量残差、未知方法和测试记录进入直接ET拟合。负水量残差按其有符号变量保留。`water_budget_residual`以田块灌溉、降雨及面积加权土层储水计算连续完整深度的残差，缺日、储水跳接或观测深度不匹配均报错。

　　耦合模型保留成熟绿色冠层的根区吸水需求，籽粒成熟仍终止同化物生产。新增每日`soil_storage_initial_mm`及`soil_storage_final_mm`仅含土层，不含冠层和积水。成熟修正通过软件物理案例检验，尚无相应配套田间水分率定。

　　农业水管理项目`model/2026-10-03_direct_et_process/`保存5675条直接ET和616条独立分类的水量残差。新参数仅在直接ET校准记录上拟合，冻结后测试；同一测试日值的平衡RMSE为小麦1.718、玉米2.407 mm/day，预测R²为0.207及0.134。实际ET仍弱。内部CV条件于全部校准冠层，不构成整个流程CV。栾城原水量残差不支持直接ET校准，既有EC数据仅作足迹未确认的补充诊断。[过程与评价](../../docs/superpowers/specs/2026-10-03-direct-et-results.md)。

### 参考需求与风速测量支持

　　`effective_et.daily_features`及`simulate_effective_et`支持显式`reference_et0_source`。默认`provided`采用给定需求；`fao56`采用同一日气象和可选实测净辐射重算参考需求及能量分量，并输出实际需求来源。冻结卡的`forcing_context`保存该选择，显式函数参数优先。命名`wind_speed_10m_m_s`保持10m支持；通用`wind_speed_m_s`需要显式`wind_height_m`。实际使用的需求与原始给定需求分别记录，旧给定模式的5675条实际ET预测及全部原CV评分保持不变。

　　`model/2026-10-03_consistent_et_forcing/`以校准CV选择三个驱动、四个结构及三种损失函数组合。小麦校准CV略改善，但测试RMSE升至1.792 mm/day、预测R²降至0.137；玉米为2.404及0.137，变化很小。该实验没有支持整体ET改善，既有直接ET参数仍为当前参考。年组CV多数留出年份包含同站更晚年份训练，不等同于向未来预测。精确气象、源文件、CV卡及102888条原生重算证据保留。[诊断与结果](../../docs/superpowers/specs/2026-10-03-consistent-et-results.md)。


　　新增`canopy_resistance.resistance_features`与`simulate_resistance_et`分别输出表面阻力特征及条件日蒸散。叶阻力、VPD响应与短波光响应均显式输入；绿色活跃LAI采用0.5倍近似，空气动力几何仍沿用参考草面。Penman–Monteith分母比仅调整冠层需求，裸土保持既有降水记忆项；70 s/m表面阻力恢复同一参考公式，绿色叶片在籽粒成熟后仍可蒸腾。该模块没有根区水量闭合或实测气孔导度验证。

　　`model/2026-10-03_canopy_resistance_et`保留48个每作物候选、16个时间折、全部5675条直接ET目标及冻结参数。小麦测试R²由0.2065微变为0.2068，玉米仍为0.1342；变化不足以支持实质改善，原直接ET率定仍为当前参考。季节样地的土壤、农事及蒸渗仪对应关系尚未完整确认，叶阻力的拟合形状不能代替实际胁迫验证。

　　`surface_depletion.prepare_surface_forcing`、`surface_fluxes`和`simulate_surface_et`提供有限裸露表层水量、REW/TEW两阶段干燥及日需求反馈。降雨与显式灌溉补充表层水，超容量水向下排出，事件蒸发受可用水和裸露面积上限约束。基础扩散通量与蒸腾代表未耦合的较深供水，不计入该表层局部收支；缺少灌溉时采用显式零灌溉情景。模块属于冻结冠层的条件ET研究路径，没有接入完整根区水量评价。

　　`model/2026-10-03_surface_depletion_et`的小麦测试R²为0.1676、RMSE为1.7601 mm/day，玉米保持0.1342及2.4075；`model/2026-10-03_station_weather_et`分别为小麦0.1560及1.7723、玉米0.1369及2.4037。两项实验均没有支持总ET性能的实质改善，既有直接ET参数仍为参考。站点温湿气压、辐射与降雨的源单位经确认，气象组合整体回退，不替换高度未确认的站点风速。全部参数采用原完整站年划分及逐年校准目标选择，测试未参与本次拟合。


## 全过程与灌溉季节检验

　　2026年10月3日联合率定资料位于农业水管理项目的`model/2026-10-03_all_process_calibration/`。禹城源说明明确收获生物量和籽粒干重为g/m²，乘以10转换为kg/ha；408条可匹配收获目标纳入独立站年划分。内部校准留出重新拟合物候与冠层，测试年此前已被检查，属于开发回归检验。

　　小麦`growth_process.grain_fill_completion_bbch=89`使籽粒库容量完成点与停止同化的成熟节点一致，潜在和水分耦合路径采用相同函数；蜡熟观测没有转为完整成熟观测。`seasonal_assessment.aggregate_et`保留预期日数、实际观测日数与缺测，累计比较严格使用同一组日期，90%覆盖仍具有缺测限定。

　　`effective_et`的天气湿润结构可接受显式`irrigation_events=[{"date":"2000-08-02","surface_wetting_mm":75.0}]`。表层湿润只影响对应日期及以后，蒸散驱动在干土与湿土分量间重新分配；该代理没有根区水状态。空事件表与无法取得管理记录具有不同证据标记，不可将空表直接判定为雨养观测。

　　原生`simulate_season`接受`et0_method="provided"`和逐日`reference_et0_mm`，已知灌溉通过现有`inputs.irrigation_events`进入守恒水量账本。每次事件需要唯一ID、日期、`amount_mm`及计量边界。研究包含三次60、75或110mm、四次75mm小麦灌溉以及一次80mm玉米播种灌溉，所有推定日历和土壤先验单独保存为情景输入；不同年份不能凭同站名称互认管理。

　　全部132个可运行作物季产生652次水量过程敏感性模拟。假设灌溉情景中的蒸腾和土壤蒸发系数仅在校准年拟合，测试ET预测R²约为小麦0.17、玉米0.09；统一补灌没有消除误差。逐日水量守恒通过数值检验，土壤储水及灌溉方式响应仍缺少同样地独立验证，区域政策推断不具备相应验证基础。


## 2026年10月5日守恒水分过程与外部检验

　　原生分层水量过程提供`soil_evaporation_method="two_stage_storage"`，干燥响应取决于实际表层储水及`readily_evaporable_fraction`；`refine_surface_layer(inputs,100)`仅拆分超过100 mm的第一层，保留土层总厚度、初始储水和水力参数。这一实现借鉴两阶段干燥原理，并非完整FAO56双作物系数算法。

　　可选`root_density_decay_m_inv`按层积分分配归一化指数根系活动；吸水同时受萎蔫点以上实际可达储水和提取速率约束。`plant_water_stress_method="root_zone_depletion"`依据面积加权的根区储水计算胁迫，`readily_available_water_fraction=0.55`为默认先验。可选`assimilation_water_stress_exponent`改变同化对实际蒸腾比例的经验响应，叶面积扩展保留原水分响应；该选项与蒸腾效率约束互斥。

　　`hydrology.root_activity_normalization="surface"`将地表有效吸水系数固定为`root_extraction_fraction_day`，系数随深度按指数衰减，并在每个土层实际生根部分积分。根区加深不会提高已有浅层根区的吸水系数。默认`rooted_volume_mean`保持根区平均活动为1；衰减系数为0时，两种计算完全一致。该系数为综合根系与土壤效应的经验提取活动，不是实测根长密度或独立根生物量。根区供水仍受实际储水、萎蔫点和逐日水量守恒约束。[APSIM根系模块的分层水分供给公式](https://github.com/APSIMInitiative/ApsimX/blob/master/Models/PMF/Organs/Root.cs)提供相应的局地提取系数计算参考。

　　可选`root_activity_background_fraction=b`在表层指数分量之外保留深层背景活动。参考深度`root_activity_reference_depth_mm=L`默认为1000 mm，背景占比限定在0—1。以毫米为深度单位，`a=root_density_decay_m_inv/1000`，相对密度为`g(z)=(1-b)*a*exp(-a*z)/(1-exp(-a*L))+b/L`。土层活动按实际生根区间积分；平均活动归一化保持生根土体平均值为1，地表归一化保持地表活动为1。背景占比为0时保留原指数计算，衰减为0或背景占比为1时得到均匀活动。根干重只能提供几何代理，不能独立测定吸水能力或水力导度；参考深度以下的背景延伸属于模型假设。逐层可达储水、萎蔫点、湿润面积及提取能力限制保持有效。

　　`hydrology.plant_water_stress_method="layer_root_depletion"`按各生根土层的水分亏缺系数降低局地潜在吸水需求。潜在需求按指数根系活动的土层积分分配，实际吸水同时受局地需求、可达储水与提取速率约束。干旱土层或干旱湿润域未满足的需求不转移至其他根区；该计算代表无补偿吸水的经验近似，不包含植物水势、盐分或缺氧响应。冠层截留蒸发先占用蒸腾能量需求，土层胁迫仅施加一次。默认根区整体亏缺计算保持独立的参照路径。[Hupet等（2003）](https://doi.org/10.1029/2003WR002046)的宏观根系吸水框架提供局地水分胁迫与深度分布相结合的计算依据；这里的局地响应使用含水量亏缺函数而非完整压力势模型。

　　`hydrology.plant_water_stress_method="compensated_layer_depletion"`增加受限的根区吸水补偿。各土层首先按局地胁迫、根系活动比例及提取能力计算基础吸水量；`root_compensation_fraction`在0—1范围内控制未满足需求向供水条件较好的生根土层转移的比例。补偿吸水仅分配至局地胁迫系数不低于根系活动加权均值的土层，并受剩余可达储水与提取能力约束。总吸水量不超过潜在需求与最佳生根土层胁迫系数的乘积；均匀干旱不因补偿而消除胁迫。该参数不影响其他吸水方法。冠层截留蒸发先占用需求，根区胁迫不重复施加。

　　补偿参数属于待校准的有效参数，默认值0.5不是田间实测根系性状。[Jarvis（2011）](https://doi.org/10.5194/hess-15-3431-2011)和[Couvreur等（2012）](https://doi.org/10.5194/hess-16-2957-2012)阐述了根区供水差异与补偿吸水的作用。上述含水量亏缺近似不等同于其根系水力网络模型，不模拟根系或叶片水势。

　　`hydrology.readily_available_water_adjustment="fao56_daily_demand"`将名义亏缺比例按逐日潜在作物蒸散需求调整：`p_daily=clip(p_nominal+0.04*(5-ET_potential),0.1,0.8)`。潜在需求为蒸腾与土壤蒸发之和，冠层截留蒸发与蒸腾竞争，不重复累加。调整作用于蒸腾胁迫，土壤蒸发仍由表层可用水约束；默认`none`保留原固定比例。逐日结果提供`root_zone_depletion_fraction`和`potential_et_demand_mm`诊断。[FAO-56第8章](https://www.fao.org/4/x0490e/x0490e0e.htm)规定该需求调整；这一近似不模拟叶水势。

　　`growth_process.transpiration_vpd_method="daily_vapour_pressure"`利用日最高与最低温度及显式实际水汽压计算有效日均VPD，最低为0.05 kPa。蒸腾效率系数单位为kPa kg干物质/kg水；此项约束不等同于叶片气孔或日间能量平衡。缺失或非法湿度输入导致显式报错。

　　132个作物季保持原71个校准与61个测试案例及整站年划分，7,397条目标不变。五个禹城校准案例采用源资料记录的灌溉事件，综合观测场名称与作物样地代码的对应属于明确假设。两阶段蒸发在校准损失中获选；指数根分布、湿度蒸腾效率及根区耗水胁迫与同化指数均未获得当前校准目标支持，不因吴桥评分而回选。

　　联合率定候选的吴桥小麦季节ET RMSE由86.49降至63.22 mm，玉米由90.69变为91.21 mm；两作物的季节ET与灌溉响应验收条件仍未同时满足。站点统一采用禹城文献初始土壤先验，吴桥采用迁移土壤参数；独立层初始含水量和设施管理对应关系尚不完整。禹城中子仪点值与日末层均值的条件诊断RMSE为0.109 m³/m³，同田对应为假设，不能视为新增完全匹配的田间验证。

　　显式覆盖`pytest.ini`的文件名白名单后，水分与科研模块检验共452项通过。先前全库遗留检验为235项通过、19项失败，全库未被判定通过。逐日水量和地上部碳量收支通过数值验证，数值守恒不代表政策情景预测已充分验证。结果、原始资料、冻结源码、图表及复算证据位于农业水管理项目的`model/2026-10-05_rootzone_stress_ET/`。


## 日内降水时序

　　`WaterDayForcing.precipitation_hourly_mm`可接受24个非负有限小时水量，分别对应输入日内0–1时至23–24时；总量必须与`precipitation_mm`一致。非整小时子步采用区间交叠积分。逐日天气可使用同名字段传入时序；空缺时保留明确标记的24小时均匀降水假设。诊断字段`precipitation_timing`区分显式小时输入和均匀假设，日内潜在ET仍均匀分布，不构成小时能量平衡模型。

　　农业水管理项目的`model/2026-10-05_hourly_rainfall_ET/`存储54个站年的ERA5-Land小时降水。AgERA5 E3累积降水日使用UTC+09，小时累积值由结束时刻向前移一小时转换为区间起点；该时钟与中国民用时间不同。小时曲线只确定日内比例，所有原日降水量及其他日气象保持不变，无法取得形状的7个案例日显式回退均匀假设。原始负增量保留归档，仅在形状处理时对微小数值负值取零。

　　短时降雨减少持续冠层截留补水。未获选的玉米小时候选使吴桥冠层蒸发均值由84.76降至60.55 mm，同时蒸腾由221.18增至247.13 mm，季节ET RMSE为92.96 mm；分量变化没有降低总ET误差。校准损失支持小麦小时候选，玉米保留前一候选；选中模型的吴桥季节ET RMSE为小麦64.50 mm、玉米91.21 mm，尚不足以支持区域灌溉优化。全部132个默认案例的共同日输出、季节汇总与末状态重现一致，显式文件名覆盖下468项水分与科研检验通过。


## Source irrigation reconciliation

The optional `canopy_v5` efficiency process accepts
`transpiration_demand_method="atmospheric"`. Potential transpiration then follows
reference ET, the crop coefficient and green-canopy cover, while actual
transpiration and radiation jointly constrain aboveground production. The
`"carbon_requirement"` definition applies the existing inverse-efficiency cap
to potential transpiration. Both definitions retain soil-water limits,
interception accounting and conservative aboveground carbon allocation.
Daily diagnostics report atmospheric demand and the resolved demand method.

The independent atmospheric-demand structure is consistent with the
[primary PCSE evapotranspiration implementation](https://github.com/ajwdewit/pcse/blob/448ed6c969af0f8afe8246e36afd0636404bd744/pcse/crop/evapotranspiration.py).
The efficiency-based growth ceiling and wet-canopy partition are OCM research
approximations; daily humidity supplies an effective air VPD rather than a
measured leaf-to-air deficit. Their predictive value requires crop-data
assessment.

`management_inputs.py` normalizes equivalent sheet/plot labels, reconciles calendar and wheat harvest years, deduplicates identical events and quarantines incompatible dates, amounts or methods. An unmatched season returns unknown management rather than measured rainfed status. Partial logs do not establish complete irrigation schedules. A recorded presowing event can replace only the explicitly assumed sowing event; later spring assumptions retain their separate status.

The audited input scenarios and joint-fit evaluation are archived at `agri_water_management/model/2026-10-05_management_joint_improvement/`. The joint parameter refit is diagnostic and rejected for poorer production/maize-ET transfer; it does not replace the retained crop cards or regional policy results. Native management regression: 10 passed; complete crop/water focused suite: 511 passed.

## Matric wetting interfaces

`hydrology.matric_interface_method="relative_arithmetic"` separates the saturated
series resistance of adjacent half-layers from their face relative mobility.
Positive mobility permits a wet cell to supply an air-dry neighboring cell;
a layer with zero saturated conductivity remains impermeable. Flux direction
follows the combined gravity and pressure gradient. Conservative bounds and
adaptive water-content increments remain active. The explicit
`"harmonic_nodal"` option reproduces the earlier interface approximation.

Arithmetic relative-conductivity weighting is documented by the USGS VS2DRT
input instructions. The saturated-resistance combination and bounded explicit
integration are this model's approximation, requiring grid and timestep
checks. The correction is a numerical result; crop and seasonal-ET gains
require assessment with calibration-only fitting and frozen testing.
