import io
import math
import random
import textwrap
import xmlrpc.client
import zipfile
import streamlit as st

# ==========================================
# 1. PAGE CONFIG & STYLING
# ==========================================
st.set_page_config(
    page_title="GAMS Logistics Model Generator & NEOS Runner",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# 2. MASTER GEOMETRY GENERATOR (SINGLE SOURCE OF TRUTH)
# ==========================================
@st.cache_data
def generate_master_geometry():
    """
    Generates deterministic master coordinates on a 100x100 grid.
    Fixed seed guarantees spatial consistency across all experiments.
    """
    random.seed(42)  # Fixed master seed
    
    # 5 Warehouses
    warehouses = {f"W{i+1}": (round(random.uniform(10, 90), 2), round(random.uniform(10, 90), 2)) for i in range(5)}
    # 15 DCs
    dcs = {f"DC{i+1}": (round(random.uniform(5, 95), 2), round(random.uniform(5, 95), 2)) for i in range(15)}
    # 300 Customers
    customers = {f"C{i+1}": (round(random.uniform(0, 100), 2), round(random.uniform(0, 100), 2)) for i in range(300)}
    
    return warehouses, dcs, customers

def calculate_euclidean_distance(p1, p2):
    return round(math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2), 2)

# ==========================================
# 3. GAMS CODE COMPILER ENGINE
# ==========================================
def compile_gams_code(num_w, num_dc, num_cust, num_periods, num_drivers, warehouse_cost, dc_cost, trans_cost_per_unit):
    master_w, master_dc, master_cust = generate_master_geometry()
    
    # Slice active node subsets from master benchmarks
    if True:
        active_w = {f"W{i + 1}": master_w[f"W{i + 1}"] for i in range(num_w)}
        active_dc = {f"D{i + 1}": master_dc[f"DC{i + 1}"] for i in range(num_dc)}
        active_cust = {f"C{i + 1}": master_cust[f"C{i + 1}"] for i in range(num_cust)}
        all_nodes = {**active_w, **active_dc, **active_cust}

        drivers = " ".join(f"n{i + 1}" for i in range(num_drivers))
        periods = " ".join(f"p{i + 1}" for i in range(num_periods))
        warehouses = " ".join(active_w)
        dcs = " ".join(active_dc)
        customers = " ".join(active_cust)
        cd_nodes = " ".join([*active_dc, *active_cust])
        wd_nodes = " ".join([*active_w, *active_dc])
        all_node_names = " ".join(all_nodes)

        # Keep table output aligned and readable in downloaded .gms files.
        table_header = "       " + " ".join(f"{node:>{max(4, len(node))}}" for node in all_nodes)
        table_rows = []
        for row_node, row_point in all_nodes.items():
            values = []
            for column_node, column_point in all_nodes.items():
                distance = calculate_euclidean_distance(row_point, column_point)
                values.append(f"{distance:g}")
            table_rows.append(f"{row_node:>5} " + " ".join(f"{value:>{max(4, len(column))}}" for value, column in zip(values, all_nodes)))
        distance_table_str = "\n".join([table_header, *table_rows])

        # Deterministic demand values keep repeated experiments reproducible.
        random.seed(101)
        demand_values = {customer: random.randint(15, 80) for customer in active_cust}
        demand_lines = "\n".join(f"      {customer} {demand_values[customer]}" for customer in active_cust)
        service_lines = "\n".join(
            [f"      {node} {random.randint(20, 50)}" for node in active_dc]
            + [f"      {customer} {demand_values[customer]}" for customer in active_cust]
        )
        supply_lines = "\n".join(
            [f"      {node} 999" for node in active_w]
            + [
                f"      {node} {max(80, int(num_cust * 25 / max(1, num_dc)))}"
                for node in active_dc
            ]
        )
        total_demand = sum(demand_values.values())
        per_warehouse_qty = math.ceil(max(0, total_demand - num_dc * max(80, int(num_cust * 25 / max(1, num_dc)))) / max(1, num_w))

    gams_template = f"""$TITLE Multi-Echelon Multi-Period Logistics Optimization Model

    ** Data: {num_cust} customers, {num_w} warehouse(s), {num_dc} DCs, {num_drivers} drivers, {num_periods} periods

    **Sets
        n        Drivers                       /{drivers}/
        p        days                          /{periods}/
        wcd      warehouses DC and Customers   /{all_node_names}/
        cd(wcd)  Customers and DC              /{cd_nodes}/
        wd(wcd)  Warehouses and DC             /{wd_nodes}/
        c(cd)    Customers                     /{customers}/
        d(wd)    Distribution Centers          /{dcs}/
        w(wd)    Warehouses                    /{warehouses}/

        Alias(cd,cdp), (c,cp), (d,dp), (wd,wdp), (wcd,wcdp), (w,wp)
    ;

    **Parameters
        S(cd)
        /
    {service_lines}
        /

        E(c)
        /
    {demand_lines}
        /

        I(wd)
        /
    {supply_lines}
        /
    ;

    **Table **T(wcd,wcdp)
    {distance_table_str}
    ;

    **Scalar **TravelCostperTime   /{trans_cost_per_unit}/;
    **Scalar **DriverCostperPeriod /{dc_cost}/;
    **Scalar **WorkingTime         /480/;
    **Scalar **M                   /9999/;

    **Scalar **NumOfCustomers;
        NumOfCustomers = **card**(c);

    **scalar **TotalDemand;
        TotalDemand = **sum**(c, E(c));

    **scalar **perwarehouseqty;
        perwarehouseqty = {per_warehouse_qty};

    **Variable **z;

    **Positive ****Variable**
        Q(p,n,d)          quantity loaded from depot d
        R(p,wd,wdp)       middle mile transfer quantity for period
        Inv(p,d)           Inventory of Distribution center at period p
        UsedTime(p,n)
        TravelCost
        DriverHiringCost
        DriversHired
        CPUTime, ElapsedTime
    ;

    **Binary ****Variable**
        x(p,n,cd,cdp)     True when there is a last mile transfer DC to Customers at period p with driver n
        y(p,n)
        h(n)
        u(p,n,d)
        B(p,wd,wdp)       True when the middle mile transfer occurs
    ;

    **Equations
        mainObjective           objective function
        RoutingIfActive         Each driver can only have routing on a particular period if he is active that period
        RoutingIfHired          Each driver must be hired if he performs any routing over the horizon
        SingleIncomingArc       Each delivery point can have at most one incoming arc per period
        SingleOutgoingArc       Each customer must have exactly one outgoing arc per period
        InflowEqualsOutflow     Flow conservation: inflow = outflow for each driver-period at each node
        WorkTimeLimit            Working time limit per driver-period
        ServicePlusTravelTime    Used time definition
        HiringCost               Driver hiring cost definition
        NoDCtoDC                 Last mile driver cannot travel from one DC to another DC
        StartDepotDef            Each active driver selects exactly one DC per period
        DepartFromDepot         Each driver must depart from exactly one DC
        ReturnToDepot            Each driver must return to the same DC
        Q_Limit                  Quantity loaded from DC allowed only if DC is selected
        Q_LoadBalance            Total delivered = total loaded from DC
        TravellingCostEq         Travel cost definition
        NumDriversEq             Number of drivers hired
        BTrueWhenFlow             Middle mile transfer activation
        NoSelfTravel              No self transfers
        onedeparture              One departure per driver-period
        onereturn                 One return per driver-period
        WarehouseTransfer         Transfers from warehouse to DC
        DCInventoryP1             Initial inventory to period 1 inventory
        DCInventoryAfter          Period p-1 inventory to subsequent inventory
    ;

    mainObjective.. z =e= DriverHiringCost + TravelCost;

    RoutingIfActive(n,p).. sum((cd,cdp), x(p,n,cd,cdp)) =l= M*y(p,n);
    RoutingIfHired(n).. sum((p,cd,cdp), x(p,n,cd,cdp)) =l= M*h(n);
    SingleIncomingArc(c).. sum((p,cd,n), x(p,n,cd,c)) =e= 1;
    SingleOutgoingArc(c).. sum((p,cdp,n), x(p,n,c,cdp)) =e= 1;
    onedeparture(p,n).. sum((cdp,d), x(p,n,d,cdp)) =e= y(p,n);
    onereturn(p,n).. sum((cdp,d), x(p,n,cdp,d)) =e= y(p,n);
    InflowEqualsOutflow(p,cd,n).. sum(cdp, x(p,n,cdp,cd)) =e= sum(cdp, x(p,n,cd,cdp));
    WorkTimeLimit(p,n).. sum((cd,cdp),(S(cdp)+T(cd,cdp))*x(p,n,cd,cdp)) =l= WorkingTime;
    ServicePlusTravelTime(p,n).. UsedTime(p,n) =e= sum((cd,cdp),(S(cdp)+T(cd,cdp))*x(p,n,cd,cdp));
    HiringCost.. DriverHiringCost =e= DriversHired*DriverCostperPeriod;
    NoDCtoDC(p,n).. sum((d,dp), x(p,n,d,dp)) =e= 0;
    StartDepotDef(p,n).. sum(d, u(p,n,d)) =e= y(p,n);
    DepartFromDepot(p,n,d).. sum(cdp, x(p,n,d,cdp)) =e= u(p,n,d);
    ReturnToDepot(p,n,d).. sum(cdp, x(p,n,cdp,d)) =e= u(p,n,d);
    Q_Limit(p,n,d).. Q(p,n,d) =l= M*u(p,n,d);
    Q_LoadBalance.. sum(c, E(c)) =e= sum((p,n,d), Q(p,n,d));
    BTrueWhenFlow(p,wd,wdp).. R(p,wd,wdp) =l= M*B(p,wd,wdp);
    WarehouseTransfer(w).. I(w) - sum((p,d), R(p,w,d)) =g= 0;
    DCInventoryP1(p,d)$(ord(p) = 1).. Inv(p,d) =e= I(d) - sum(dp, R(p,d,dp)) - sum(n, Q(p,n,d));
    DCInventoryAfter(p,d)$(ord(p) > 1).. Inv(p,d) =e= Inv(p-1,d) + sum(wdp, R(p-1,wdp,d)) - sum(dp, R(p,d,dp)) - sum(n, Q(p,n,d));
    NoSelfTravel(p,wd,wd).. B(p,wd,wd) =e= 0;
    TravellingCostEq.. TravelCost =e= sum((p,n,cd,cdp), TravelCostperTime*(S(cd)+T(cd,cdp))*x(p,n,cd,cdp)) + sum((p,wd,wdp), B(p,wd,wdp)*T(wd,wdp)*TravelCostperTime);
    NumDriversEq.. DriversHired =e= sum(n, h(n));

    **Model **MTSP /ALL/;
    **Solve** MTSP **minimizing** z **using** **MIP**;

    CPUTime.l = MTSP.resusd;
    ElapsedTime.l = timeElapsed;

    **option** x:0:0:1;
    **option** Q:0:0:1;
    **option** R:0:0:1;
    **option** Inv:0:0:1;
    **option** B:0:0:1;

    **Display** TotalDemand, CPUTime.l, ElapsedTime.l, UsedTime.l;
    """
    return textwrap.dedent(gams_template).strip()

# ==========================================
# 4. NEOS SERVER INTERFACE
# ==========================================
def submit_to_neos(gams_code, email="researcher@ndsu.edu"):
    """Submits GAMS execution string to NEOS XML-RPC endpoint."""
    neos = xmlrpc.client.ServerProxy("https://neos-server.org:9332")
    
    xml_template = f"""<neos>
<category>milp</category>
<solver>CPLEX</solver>
<inputMethod>GAMS</inputMethod>
<email>{email}</email>
<model><![CDATA[{gams_code}]]></model>
</neos>"""

    try:
        job_number, password = neos.submitJob(xml_template)
        return job_number, password, "Submitted Successfully"
    except Exception as e:
        return None, None, str(e)

def get_neos_status(job_number, password):
    neos = xmlrpc.client.ServerProxy("https://neos-server.org:9332")
    try:
        status = neos.getJobStatus(job_number, password)
        return status
    except Exception as e:
        return str(e)

def get_neos_final_results(job_number, password):
    neos = xmlrpc.client.ServerProxy("https://neos-server.org:9332")
    try:
        results = neos.getFinalResults(job_number, password)
        return results.data.decode('utf-8')
    except Exception as e:
        return f"Error retrieving output: {str(e)}"

# ==========================================
# 5. STREAMLIT USER INTERFACE
# ==========================================

# Initialize Session State
if "neos_jobs" not in st.session_state:
    st.session_state["neos_jobs"] = []
if "generated_code" not in st.session_state:
    st.session_state["generated_code"] = ""

st.title("📦 GAMS Code Generator & Automated NEOS Runner")
st.markdown("Generate spatially consistent logistics network formulations backed by a **5 W / 15 DC / 300 Customer** benchmark coordinate grid.")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("🕹️ Baseline Parameter Settings")

s_num_w = st.sidebar.slider("Warehouses (Max 5)", 1, 5, 3)
s_num_dc = st.sidebar.slider("Distribution Centers (Max 15)", 1, 15, 8)
s_num_cust = st.sidebar.slider("Customers (Max 300)", 10, 300, 50, step=10)
s_num_periods = st.sidebar.slider("Planning Periods", 1, 12, 4)
s_num_drivers = st.sidebar.number_input("Driver Count", min_value=1, value=10)

st.sidebar.subheader("Cost Structure")
s_w_cost = st.sidebar.number_input("Warehouse Fixed Cost ($)", value=10000)
s_dc_cost = st.sidebar.number_input("DC Fixed Cost ($)", value=2500)
s_trans_cost = st.sidebar.number_input("Trans Cost ($/km/unit)", value=1.5, step=0.1)

# Main Application Tabs
tab_single, tab_batch, tab_neos = st.tabs(["📄 Single Model Generator", "📦 Batch Generator & Zip", "🚀 NEOS Job Monitor"])

# ==========================================
# TAB 1: SINGLE MODEL GENERATION & MANUAL EDITOR
# ==========================================
with tab_single:
    col_ctrl, col_main = st.columns([1, 2])
    
    with col_ctrl:
        st.subheader("Model Synthesis")
        if st.button("Generate GAMS Code", type="primary", use_container_width=True):
            st.session_state["generated_code"] = compile_gams_code(
                s_num_w, s_num_dc, s_num_cust, s_num_periods, s_num_drivers,
                s_w_cost, s_dc_cost, s_trans_cost
            )
            st.success("GAMS model compiled using master benchmark spatial distances!")

        st.divider()
        st.subheader("NEOS Direct Submit")
        user_email = st.text_input("NEOS User Email", value="researcher@ndsu.edu")
        if st.button("Submit Current Code to NEOS", use_container_width=True):
            if not st.session_state["generated_code"]:
                st.error("Please generate or edit a GAMS code model first.")
            else:
                job_id, pwd, msg = submit_to_neos(st.session_state["generated_code"], user_email)
                if job_id:
                    st.session_state["neos_jobs"].append({
                        "id": job_id,
                        "password": pwd,
                        "status": "Submitted",
                        "code": st.session_state["generated_code"][:200] + "..."
                    })
                    st.success(f"Job successfully sent to NEOS! Job ID: **{job_id}**")
                else:
                    st.error(f"Submission failed: {msg}")

    with col_main:
        st.subheader("Manual Code Editor & Viewer")
        if st.session_state["generated_code"]:
            edited_code = st.text_area(
                "Modify your GAMS model manually below:",
                value=st.session_state["generated_code"],
                height=520
            )
            st.session_state["generated_code"] = edited_code
            
            st.download_button(
                label="💾 Download Current GAMS File (.gms)",
                data=st.session_state["generated_code"],
                file_name="logistics_model.gms",
                mime="text/plain"
            )
        else:
            st.info("Click 'Generate GAMS Code' on the left panel to populate the workspace.")

# ==========================================
# TAB 2: BATCH CODE GENERATOR & ZIP EXPORT
# ==========================================
with tab_batch:
    st.subheader("Batch Parameter Sweep Configuration")
    st.write("Keep core grid locations constant while sweeping through parameter variations across multiple `.gms` script files.")
    
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        fixed_param = st.selectbox("Fixed Variable across Batch", ["Locations & Grid Coordinates"], disabled=True)
        sweep_customers = st.multiselect("Customer Node Counts to Sweep", options=[25, 50, 100, 200, 300], default=[25, 50, 100])
        sweep_periods = st.multiselect("Planning Periods to Sweep", options=[1, 3, 6, 12], default=[3, 6])
    
    with col_b2:
        sweep_drivers = st.multiselect("Driver Counts to Sweep", options=[5, 10, 20], default=[10])

    if st.button("Generate Batch Package (.zip)", type="primary"):
        zip_buffer = io.BytesIO()
        batch_count = 0
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for c_val in sweep_customers:
                for p_val in sweep_periods:
                    for d_val in sweep_drivers:
                        batch_count += 1
                        code_str = compile_gams_code(
                            s_num_w, s_num_dc, c_val, p_val, d_val,
                            s_w_cost, s_dc_cost, s_trans_cost
                        )
                        filename = f"exp_C{c_val}_P{p_val}_D{d_val}.gms"
                        zip_file.writestr(filename, code_str)
        
        zip_buffer.seek(0)
        st.success(f"Generated {batch_count} GAMS script files maintaining spatial coordinate consistency!")
        st.download_button(
            label=f"💾 Download All {batch_count} GAMS Files (.zip)",
            data=zip_buffer,
            file_name="gams_batch_experiments.zip",
            mime="application/zip"
        )

# ==========================================
# TAB 3: NEOS SERVER MONITORING & LOGS
# ==========================================
with tab_neos:
    st.subheader("NEOS Server Job Tracker")
    
    if len(st.session_state["neos_jobs"]) == 0:
        st.info("No active NEOS jobs submitted during this session.")
    else:
        for idx, job in enumerate(st.session_state["neos_jobs"]):
            with st.expander(f"Job ID: {job['id']} (Password: {job['password']})"):
                col_st, col_act = st.columns([2, 1])
                
                with col_st:
                    st.write(f"**Preview:** `{job['code']}`")
                
                with col_act:
                    if st.button(f"Check Status #{job['id']}", key=f"stat_{idx}"):
                        current_status = get_neos_status(job['id'], job['password'])
                        job['status'] = current_status
                        st.write(f"Current Status: **{current_status}**")
                    
                    if st.button(f"Fetch Output Log #{job['id']}", key=f"res_{idx}"):
                        log_output = get_neos_final_results(job['id'], job['password'])
                        st.code(log_output, language="text")
