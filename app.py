import io
import base64
import concurrent.futures
import math
import random
import re
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
    return int(round(math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)))

# ==========================================
# 3. GAMS CODE COMPILER ENGINE
# ==========================================
def compile_gams_code(
    num_w,
    num_dc,
    num_cust,
    num_periods,
    num_drivers,
    dc_cost,
    trans_cost_per_unit,
    warehouse_inventory,
    dc_inventory,
):
    master_w, master_dc, master_cust = generate_master_geometry()
    active_w = [f"W{i}" for i in range(1, num_w + 1)]
    active_dc = [f"D{i}" for i in range(1, num_dc + 1)]
    active_cust = [f"C{i}" for i in range(1, num_cust + 1)]
    all_nodes = active_w + active_dc + active_cust
    drivers = " ".join(f"n{i}" for i in range(1, num_drivers + 1))
    periods = " ".join(f"p{i}" for i in range(1, num_periods + 1))
    service_values = {node: 32 for node in active_dc}
    demand_seed = random.Random(101)
    service_seed = random.Random(202)
    demand_values = {customer: demand_seed.randint(10, 35) for customer in active_cust}
    service_values.update({customer: service_seed.randint(10, 45) for customer in active_cust})
    distance_rows = {
        "W1": [0, 22, 13, 21, 37, 21, 29, 18, 35, 11, 33, 24, 27, 13, 39, 22, 12, 27, 19, 36, 30, 29, 38],
        "D1": [22, 0, 37, 21, 28, 16, 32, 12, 11, 31, 17, 34, 19, 12, 37, 17, 37, 13, 22, 18, 24, 30, 36],
        "D2": [13, 37, 0, 21, 15, 21, 21, 16, 31, 18, 32, 39, 31, 30, 12, 29, 30, 15, 27, 33, 17, 15, 24],
        "C1": [21, 21, 21, 0, 22, 18, 39, 30, 32, 27, 17, 31, 20, 36, 34, 34, 11, 17, 36, 11, 35, 20, 22],
        "C2": [37, 28, 15, 22, 0, 18, 12, 16, 39, 40, 28, 38, 32, 20, 16, 30, 25, 22, 38, 39, 30, 24, 14],
        "C3": [21, 16, 21, 18, 18, 0, 18, 14, 17, 33, 27, 27, 18, 33, 28, 23, 38, 28, 22, 21, 17, 14, 26],
        "C4": [29, 32, 21, 39, 12, 18, 0, 25, 12, 34, 11, 37, 13, 14, 30, 15, 35, 31, 23, 29, 12, 22, 22],
        "C5": [18, 12, 16, 30, 16, 14, 25, 0, 29, 24, 26, 18, 27, 37, 40, 10, 31, 33, 13, 31, 38, 27, 34],
        "C6": [35, 11, 31, 32, 39, 17, 12, 29, 0, 18, 34, 30, 20, 13, 19, 23, 15, 24, 10, 40, 33, 38, 33],
        "C7": [11, 31, 18, 27, 40, 33, 34, 24, 18, 0, 18, 26, 34, 15, 26, 39, 13, 37, 30, 19, 36, 30, 26],
        "C8": [33, 17, 32, 17, 28, 27, 11, 26, 34, 18, 0, 29, 16, 14, 21, 34, 15, 27, 40, 34, 39, 26, 39],
        "C9": [24, 34, 39, 31, 38, 27, 37, 18, 30, 26, 29, 0, 10, 29, 20, 25, 10, 13, 39, 21, 38, 36, 35],
        "C10": [27, 19, 31, 20, 32, 18, 13, 27, 20, 34, 16, 10, 0, 19, 17, 11, 17, 38, 28, 40, 12, 12, 33],
        "C11": [13, 12, 30, 36, 20, 33, 14, 37, 13, 15, 14, 29, 19, 0, 25, 36, 12, 34, 27, 34, 14, 14, 31],
        "C12": [39, 37, 12, 34, 16, 28, 30, 40, 19, 26, 21, 20, 17, 25, 0, 25, 40, 27, 15, 18, 26, 37, 29],
        "C13": [22, 17, 29, 34, 30, 23, 15, 10, 23, 39, 34, 25, 11, 36, 25, 0, 23, 40, 16, 39, 27, 34, 33],
        "C14": [12, 37, 30, 11, 25, 38, 35, 31, 15, 13, 15, 10, 17, 12, 40, 23, 0, 32, 16, 32, 19, 22, 31],
        "C15": [27, 13, 15, 17, 22, 28, 31, 33, 24, 37, 27, 13, 38, 34, 27, 40, 32, 0, 30, 21, 24, 38, 26],
        "C16": [19, 22, 27, 36, 38, 22, 23, 13, 10, 30, 40, 39, 28, 27, 15, 16, 16, 30, 0, 24, 13, 17, 17],
        "C17": [36, 18, 33, 11, 39, 21, 29, 31, 40, 19, 34, 21, 40, 34, 18, 39, 32, 21, 24, 0, 12, 20, 10],
        "C18": [30, 24, 17, 35, 30, 17, 12, 38, 33, 36, 39, 38, 12, 14, 26, 27, 19, 24, 13, 12, 0, 28, 27],
        "C19": [29, 30, 15, 20, 24, 14, 22, 27, 38, 30, 26, 36, 12, 14, 37, 34, 22, 38, 17, 20, 28, 0, 17],
        "C20": [38, 36, 24, 22, 14, 26, 22, 34, 33, 26, 39, 35, 33, 31, 29, 33, 31, 26, 17, 10, 27, 17, 0],
    }
    coordinates = {
        **{f"W{i}": master_w[f"W{i}"] for i in range(1, num_w + 1)},
        **{f"D{i}": master_dc[f"DC{i}"] for i in range(1, num_dc + 1)},
        **{f"C{i}": master_cust[f"C{i}"] for i in range(1, num_cust + 1)},
    }
    distance_rows = {
        row_node: [calculate_euclidean_distance(coordinates[row_node], coordinates[column_node]) for column_node in all_nodes]
        for row_node in all_nodes
    }
    column_width = max(4, max(len(node) for node in all_nodes) + 1)
    table_header = " " * 5 + " ".join(f"{node:>{column_width}}" for node in all_nodes)
    table_rows = [
        f"{row_node:>{column_width}} " + " ".join(f"{distance:>{column_width}}" for distance in distance_rows[row_node])
        for row_node in all_nodes
    ]
    distance_table_str = "\n".join([table_header, *table_rows])
    service_lines = "\n".join(f"      {node} {service_values[node]}" for node in active_dc + active_cust)
    demand_lines = "\n".join(f"      {node} {demand_values[node]}" for node in active_cust)
    supply_lines = "\n".join(
        [*[f"      {node} {warehouse_inventory}" for node in active_w],
         *[f"      {node} {dc_inventory}" for node in active_dc]]
    )
    per_warehouse_qty = math.ceil(
        max(0, sum(demand_values.values()) - num_dc * dc_inventory) / len(active_w)
    )
    wcd_nodes = ", ".join(all_nodes)
    cd_nodes = ", ".join(active_dc + active_cust)
    wd_nodes = ", ".join(active_w + active_dc)
    customer_nodes = ", ".join(active_cust)
    dc_nodes = ", ".join(active_dc)
    warehouse_nodes = ", ".join(active_w)

    gams_template = f"""$TITLE mTSP experimentation - Journal Instance W{num_w} D{num_dc} C{num_cust}
$offlisting

* Data: {num_cust} customers, {num_w} warehouse, {num_dc} DCs, {num_drivers} drivers, {num_periods} periods

Sets
    n        Drivers                       /n1*n{num_drivers}/
    p        days                          /p1*p{num_periods}/
    wcd      warehouses DC and Customers   /{wcd_nodes}/
    cd(wcd)  Customers and DC              /{cd_nodes}/
    wd(wcd)  Warehouses and DC              /{wd_nodes}/
    c(cd)    Customers                     /{customer_nodes}/
    d(wd)    Distribution Centers          /{dc_nodes}/
    w(wd)    Warehouses                    /{warehouse_nodes}/

    Alias(cd,cdp), (c,cp), (d,dp), (wd,wdp), (wcd,wcdp), (w,wp)
;

Parameters
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

Table T(wcd,wcdp)
{distance_table_str}
;

Scalar TravelCostperTime   /2/;
Scalar DriverCostperPeriod /160/;
Scalar WorkingTime         /480/;
Scalar M                   /9999/;

Scalar NumOfCustomers;
    NumOfCustomers = card(c);

Scalar TotalDemand;
    TotalDemand = sum(c, E(c));

Scalar perwarehouseqty;
    perwarehouseqty = max(0, ceil(sum(c, E(c)) - sum(d, I(d))) / card(w));
    I(w) = perwarehouseqty;

Variable z;

Positive Variable
    Q(p,n,d)          quantity loaded from depot d
    R(p,wd,wdp)       middle mile transfer quantity for period
    Inv(p, d)         Inventory of Distribution center at period p
    UsedTime(p,n)
    TravelCost
    DriverHiringCost
    DriversHired
    CPUTime, ElapsedTime
;

Binary Variable
    x     True when there is a last mile transfer DC to Customers at period p with driver n
    y(p,n)
    h(n)
    u(p,n,d)
    B(p,wd,wdp)       True when the middle mile transfer occurs
;

Equations
    mainObjective
    RoutingIfActive
    RoutingIfHired
    SingleIncomingArc
    SingleOutgoingArc
    InflowEqualsOutflow
    WorkTimeLimit
    MTZConstraint
    ServicePlusTravelTime
    HiringCost
    NoDCtoDC
    StartDepotDef
    DepartFromDepot
    ReturnToDepot
    Q_Limit
    Q_LoadBalance
    TravellingCostEq
    NumDriversEq
    BTrueWhenFlow
    NoSelfTravel
    onedeparture
    onereturn
    WarehouseTransfer
    DCInventoryP1
    DCInventoryAfter
;

mainObjective.. z =e= DriverHiringCost + TravelCost;

RoutingIfActive(n,p).. sum((cd,cdp), x(p,n,cd,cdp)) =l= M*y(p,n);
RoutingIfHired(n).. sum((p,cd,cdp), x(p,n,cd,cdp)) =l= M*h(n);
SingleIncomingArc(c).. sum((p,cd,n), x(p,n,cd,c)) =e= 1;
SingleOutgoingArc(c).. sum((p,cdp,n), x(p,n,c,cdp)) =e= 1;
onedeparture(p,n).. sum((cdp,d), x(p,n,d,cdp)) =e= y(p,n);
onereturn(p,n).. sum((cdp,d), x(p,n,cdp,d)) =e= y(p,n);
InflowEqualsOutflow(p, cd,n).. sum((cdp), x(p,n,cdp,cd)) =e= sum((cdp), x(p,n,cd,cdp));
WorkTimeLimit(p,n).. sum((cd,cdp),(S(cdp)+T(cd,cdp))*x(p,n,cd,cdp)) =l= WorkingTime;
MTZConstraint(p,n,c,cp).. ord(c)-ord(cp)+NumOfCustomers*x(p,n,c,cp) =l= NumOfCustomers-1;
ServicePlusTravelTime(p,n).. UsedTime(p,n) =e= sum((cd,cdp),(S(cdp)+T(cd,cdp))*x(p,n,cd,cdp));
HiringCost.. DriverHiringCost =e= sum((p,n),y(p,n))*DriverCostperPeriod;
NoDCtoDC(p,n).. sum((d,dp), x(p,n,d,dp)) =e= 0;
StartDepotDef(p,n).. sum(d, u(p,n,d)) =e= y(p,n);
DepartFromDepot(p,n,d).. sum(cdp, x(p,n,d,cdp)) =e= u(p,n,d);
ReturnToDepot(p,n,d).. sum(cdp, x(p,n,cdp,d)) =e= u(p,n,d);
Q_Limit(p,n,d).. Q(p,n,d) =l= M*u(p,n,d);
Q_LoadBalance.. sum(c, E(c)) =e= sum((p,n,d), Q(p,n,d));
BTrueWhenFlow(p, wd, wdp).. R(p, wd, wdp) =l= M * B(p, wd, wdp);
WarehouseTransfer(w).. I(w) - sum((p,d),R(p,w,d)) =g= 0;
DCInventoryP1(p,d)$(ord(p) = 1).. Inv(p, d) =e= I(d) - sum(dp, R(p, d, dp)) - sum(n, Q(p, n, d));
DCInventoryAfter(p, d)$(ord(p) > 1).. Inv(p, d) =e= Inv(p-1, d) + sum(wdp, R(p-1, wdp, d)) - sum(dp, R(p, d, dp)) - sum(n, Q(p, n, d));
NoSelfTravel(p,wd,wd).. B(p,wd,wd) =e= 0;
TravellingCostEq.. TravelCost =e= sum((p,n,cd,cdp), TravelCostperTime * (S(cd)+T(cd,cdp)) * x(p,n,cd,cdp)) + sum((p,wd,wdp), B(p,wd,wdp) * T(wd,wdp) * TravelCostperTime);
NumDriversEq.. DriversHired =e= sum(n, h(n));

Model MTSP /ALL/;
Solve MTSP minimizing z using MIP;

CPUTime.l     = MTSP.resusd;
ElapsedTime.l = timeElapsed;

option x:0:0:1;
option Q:0:0:1;
option R:0:0:1;
option Inv:0:0:1;
option B:0:0:1;

Display Totaldemand, CPUTime.l, ElapsedTime.l, UsedTime.l;
Display x.l, B.l, R.l, Q.l, Inv.l, perwarehouseqty, TravelCost.l, DriverHiringCost.l, DriversHired.l;
"""
    return textwrap.dedent(gams_template).strip()

# ==========================================
# 4. NEOS SERVER INTERFACE
# ==========================================
NEOS_ENDPOINT = "https://neos-server.org:3333"

class TimeoutTransport(xmlrpc.client.SafeTransport):
    """Bounds NEOS XML-RPC calls so a slow server fails fast instead of hanging the UI."""
    def __init__(self, timeout, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection

def get_neos_proxy(timeout=30):
    return xmlrpc.client.ServerProxy(NEOS_ENDPOINT, transport=TimeoutTransport(timeout))

def submit_to_neos(gams_code, email="researcher@ndsu.edu"):
    """Submits GAMS execution string to NEOS XML-RPC endpoint."""
    neos = get_neos_proxy(timeout=60)

    # NEOS requires a <document> root element; a <neos> root is silently rejected.
    xml_template = f"""<document>
<category>milp</category>
<solver>Cplex</solver>
<inputMethod>GAMS</inputMethod>

<model><![CDATA[
{gams_code}
]]></model>

<email>{email}</email>
</document>"""

    try:
        job_number, password = neos.submitJob(xml_template)
        if job_number == 0:
            return None, None, password
        return job_number, password, "Submitted Successfully"
    except Exception as e:
        return None, None, str(e)

def get_neos_status(job_number, password):
    # A short timeout here was misread as a real NEOS status; give the round trip more room.
    neos = get_neos_proxy(timeout=45)
    try:
        status = neos.getJobStatus(job_number, password)
        return status
    except Exception as e:
        return str(e)

def get_neos_final_results(job_number, password):
    # getFinalResults blocks on NEOS until the job finishes, so allow a long wait.
    neos = get_neos_proxy(timeout=600)
    try:
        results = neos.getFinalResults(job_number, password)
        return results.data.decode('utf-8')
    except Exception as e:
        return f"Error retrieving output: {str(e)}"

def download_text_automatically(filename, content):
    encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
    st.markdown(
        f'''<a id="download-report" download="{filename}" href="data:text/plain;base64,{encoded_content}"></a>
        <script>document.getElementById("download-report").click();</script>''',
        unsafe_allow_html=True,
    )

# ==========================================
# 5. STREAMLIT USER INTERFACE
# ==========================================

# Initialize Session State
if "neos_jobs" not in st.session_state:
    st.session_state["neos_jobs"] = []
if "generated_code" not in st.session_state:
    st.session_state["generated_code"] = ""
if "batch_models" not in st.session_state:
    st.session_state["batch_models"] = []

st.title("📦 GAMS Code Generator & Automated NEOS Runner")
st.markdown("Generate spatially consistent logistics network formulations backed by a **5 W / 15 DC / 300 Customer** benchmark coordinate grid.")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.header("🕹️ Baseline Parameter Settings")

s_num_w = st.sidebar.slider("Warehouses", 1, 5, 1)
s_num_dc = st.sidebar.slider("Distribution Centers", 1, 15, 2)
s_num_cust = st.sidebar.slider("Customers", 1, 300, 20)
s_num_periods = st.sidebar.slider("Planning Periods", 1, 5, 5)
s_num_drivers = st.sidebar.number_input("Driver Count", min_value=1, max_value=20, value=3)

st.sidebar.subheader("Cost Structure")
s_dc_cost = st.sidebar.number_input("Driver Cost per Period ($)", value=160)
s_trans_cost = st.sidebar.number_input("Travel Cost per Time", value=2, step=1)

st.sidebar.subheader("Inventory Settings")
s_warehouse_inventory = st.sidebar.number_input("Warehouse Inventory", min_value=0, value=999, step=1)
s_dc_inventory = st.sidebar.number_input("DC Inventory", min_value=0, value=80, step=1)

# Main Application Tabs
tab_single, tab_batch, tab_neos = st.tabs(["📄 Single Model Generator", "📦 Batch Generator & Zip", "🚀 NEOS Job View"])

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
                s_dc_cost, s_trans_cost, s_warehouse_inventory, s_dc_inventory
            )
            st.success("GAMS model compiled using master benchmark spatial distances!")

        st.divider()
        st.subheader("NEOS Direct Submit")
        st.caption(f"Optimizer: CPLEX via {NEOS_ENDPOINT}")
        user_email = st.text_input("NEOS User Email", value="researcher@ndsu.edu")
        single_submit_status = st.empty()
        if st.button("Submit Current Code to NEOS", use_container_width=True):
            if not st.session_state["generated_code"]:
                st.error("Please generate or edit a GAMS code model first.")
            else:
                single_submit_status.info("Sending current GAMS file to NEOS CPLEX...")
                job_id, pwd, msg = submit_to_neos(st.session_state["generated_code"], user_email)
                if job_id:
                    st.session_state["neos_jobs"].append({
                        "id": job_id,
                        "password": pwd,
                        "status": "Submitted",
                        "filename": f"{s_num_cust}C-{s_num_dc}DC-{s_num_w}WH-{s_num_periods}periods-{s_num_drivers}Drivers.GMS",
                        "code": st.session_state["generated_code"][:200] + "..."
                    })
                    single_submit_status.success(f"File sent successfully. Job ID: {job_id} | Password: {pwd}")
                else:
                    single_submit_status.error(f"Submission failed: {msg}")

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
                file_name=f"{s_num_cust}C-{s_num_dc}DC-{s_num_w}WH-{s_num_periods}periods-{s_num_drivers}Drivers.GMS",
                mime="text/plain"
            )
        else:
            st.info("Click 'Generate GAMS Code' on the left panel to populate the workspace.")

# ==========================================
# TAB 2: BATCH CODE GENERATOR & ZIP EXPORT
# ==========================================
def get_batch_values(label, minimum, maximum, default, key):
    mode = st.radio(
        f"{label} definition",
        ["Static", "Dynamic"],
        horizontal=True,
        key=f"{key}_mode",
    )

    if mode == "Static":
        value = st.number_input(
            f"{label} value",
            min_value=minimum,
            max_value=maximum,
            value=default,
            step=1,
            key=f"{key}_static",
        )
        return [int(value)]

    start, end, step = st.columns(3)
    with start:
        start_value = st.number_input(
            f"{label} start",
            min_value=minimum,
            max_value=maximum,
            value=default,
            step=1,
            key=f"{key}_start",
        )
    with end:
        end_value = st.number_input(
            f"{label} end",
            min_value=minimum,
            max_value=maximum,
            value=default,
            step=1,
            key=f"{key}_end",
        )
    with step:
        step_value = st.number_input(
            f"{label} step",
            min_value=1,
            max_value=maximum,
            value=1,
            step=1,
            key=f"{key}_step",
        )

    if start_value > end_value:
        st.error(f"{label} start must be less than or equal to its end value.")
        return []

    return list(range(int(start_value), int(end_value) + 1, int(step_value)))

with tab_batch:
    st.subheader("Batch Parameter Sweep Configuration")
    st.write("Keep core grid locations constant while sweeping through parameter variations across multiple `.gms` script files.")
    
    group_by = st.radio(
        "Group files by",
        ["None", "Warehouses", "Distribution centers", "Customers", "Planning periods", "Drivers"],
        horizontal=True,
        key="batch_group_by",
    )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        fixed_param = st.selectbox("Fixed Variable across Batch", ["Locations & Grid Coordinates"], disabled=True)
        sweep_warehouses = get_batch_values("Warehouses", 1, 5, s_num_w, "batch_warehouses")
        sweep_dcs = get_batch_values("Distribution centers", 1, 15, s_num_dc, "batch_dcs")
        sweep_customers = get_batch_values("Customers", 1, 300, s_num_cust, "batch_customers")
        sweep_periods = get_batch_values("Planning periods", 1, 5, s_num_periods, "batch_periods")
    
    with col_b2:
        sweep_drivers = get_batch_values("Drivers", 1, 20, s_num_drivers, "batch_drivers")

    if st.button("Generate Batch Package (.zip)", type="primary"):
        zip_buffer = io.BytesIO()
        batch_count = 0
        st.session_state["batch_models"] = []
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for w_val in sweep_warehouses:
                for dc_val in sweep_dcs:
                    for c_val in sweep_customers:
                        for p_val in sweep_periods:
                            for d_val in sweep_drivers:
                                batch_count += 1
                                code_str = compile_gams_code(
                                    w_val, dc_val, c_val, p_val, d_val,
                                    s_dc_cost, s_trans_cost,
                                    s_warehouse_inventory, s_dc_inventory
                                )
                                filename = f"{c_val}C-{dc_val}DC-{w_val}WH-{p_val}periods-{d_val}Drivers.GMS"
                                if group_by == "Warehouses":
                                    filename = f"{w_val}WH/{filename}"
                                elif group_by == "Distribution centers":
                                    filename = f"{dc_val}DC/{filename}"
                                elif group_by == "Customers":
                                    filename = f"{c_val}C/{filename}"
                                elif group_by == "Planning periods":
                                    filename = f"{p_val}periods/{filename}"
                                elif group_by == "Drivers":
                                    filename = f"{d_val}Drivers/{filename}"
                                st.session_state["batch_models"].append({
                                    "filename": filename,
                                    "code": code_str,
                                })
                                zip_file.writestr(filename, code_str)
        
        zip_buffer.seek(0)
        st.success(f"Generated {batch_count} GAMS script files maintaining spatial coordinate consistency!")
        st.download_button(
            label=f"💾 Download All {batch_count} GAMS Files (.zip)",
            data=zip_buffer,
            file_name="gams_batch_experiments.zip",
            mime="application/zip"
        )

    st.subheader("Submit Batch Files to NEOS")
    NEOS_FILES_PER_EMAIL = 15
    batch_models = st.session_state["batch_models"]
    emails_needed = max(1, math.ceil(len(batch_models) / NEOS_FILES_PER_EMAIL)) if batch_models else 1
    st.caption(f"NEOS accepts up to {NEOS_FILES_PER_EMAIL} files per email. Add one email per {NEOS_FILES_PER_EMAIL} files.")
    num_emails = st.number_input(
        "Number of NEOS emails to use",
        min_value=1,
        value=emails_needed,
        step=1,
        key="batch_num_emails",
    )
    batch_emails = [
        st.text_input(f"NEOS email #{i + 1} (files {i * NEOS_FILES_PER_EMAIL + 1}-{(i + 1) * NEOS_FILES_PER_EMAIL})",
                      value="researcher@ndsu.edu" if i == 0 else "",
                      key=f"batch_email_{i}")
        for i in range(int(num_emails))
    ]
    if batch_models:
        st.caption(f"{len(batch_models)} batch file(s) ready for NEOS submission.")
    batch_submit_status = st.empty()
    if st.button("Submit Batch Files to NEOS", disabled=not batch_models):
        required_emails = max(1, math.ceil(len(batch_models) / NEOS_FILES_PER_EMAIL))
        missing_emails = [i for i in range(required_emails) if not batch_emails[i].strip()] if required_emails <= len(batch_emails) else list(range(len(batch_emails), required_emails))
        if len(batch_emails) < required_emails or missing_emails:
            st.error(f"Provide {required_emails} email(s) to cover {len(batch_models)} file(s) at {NEOS_FILES_PER_EMAIL} files per email.")
        else:
            submission_lines = ["Batch NEOS submission credentials", ""]
            progress = st.progress(0)
            for index, batch_model in enumerate(batch_models):
                submission_email = batch_emails[index // NEOS_FILES_PER_EMAIL]
                batch_submit_status.info(
                    f"Sending file {index + 1} of {len(batch_models)}: {batch_model['filename']}"
                )
                job_id, password, message = submit_to_neos(batch_model["code"], submission_email)
                if job_id:
                    st.session_state["neos_jobs"].append({
                        "id": job_id,
                        "password": password,
                        "status": "Submitted",
                        "filename": batch_model["filename"],
                        "code": batch_model["code"][:200] + "...",
                    })
                    submission_lines.append(
                        f"{batch_model['filename']} | Email: {submission_email} | "
                        f"Job ID: {job_id} | Password: {password}"
                    )
                    batch_submit_status.success(
                        f"Sent {index + 1} of {len(batch_models)}: {batch_model['filename']} "
                        f"(Job ID: {job_id})"
                    )
                else:
                    submission_lines.append(
                        f"{batch_model['filename']} | Email: {submission_email} | Failed: {message}"
                    )
                    batch_submit_status.error(
                        f"Failed {index + 1} of {len(batch_models)}: {batch_model['filename']}"
                    )
                progress.progress((index + 1) / len(batch_models))

            credentials_report = "\n".join(submission_lines) + "\n"
            st.session_state["batch_credentials_report"] = credentials_report
            st.success("Batch files submitted to NEOS. Credentials report downloaded.")
            download_text_automatically("batch_neos_credentials.txt", credentials_report)
            st.download_button(
                "Download NEOS credentials report",
                data=credentials_report,
                file_name="batch_neos_credentials.txt",
                mime="text/plain",
                key="batch_credentials_download",
            )

# ==========================================
# TAB 3: NEOS JOB VIEW
# ==========================================
with tab_neos:
    st.subheader("NEOS Job View")
    st.caption(f"Submitted models use the CPLEX optimizer through {NEOS_ENDPOINT}.")

    uploaded_credentials = st.file_uploader(
        "Upload a NEOS credentials text file",
        type=["txt"],
        key="neos_credentials_upload",
    )
    if uploaded_credentials is not None:
        uploaded_text = uploaded_credentials.getvalue().decode("utf-8", errors="replace")
        uploaded_jobs = []
        for line in uploaded_text.splitlines():
            job_match = re.search(r"Job ID:\s*([^|\s]+)", line)
            password_match = re.search(r"Password:\s*([^|\s]+)", line)
            if job_match and password_match:
                filename = line.split(" | ", 1)[0]
                uploaded_jobs.append((filename, int(job_match.group(1)), password_match.group(1)))

        if uploaded_jobs:
            st.caption(f"Found {len(uploaded_jobs)} job credential(s) in the uploaded file.")
            if "uploaded_job_statuses" not in st.session_state:
                st.session_state["uploaded_job_statuses"] = {}
            job_statuses = st.session_state["uploaded_job_statuses"]

            if st.button("Check All Statuses", key="check_all_uploaded_statuses"):
                with st.spinner(f"Checking status for {len(uploaded_jobs)} job(s)..."):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                        futures = {
                            pool.submit(get_neos_status, job_id, password): job_id
                            for _, job_id, password in uploaded_jobs
                        }
                        for future in concurrent.futures.as_completed(futures):
                            job_statuses[futures[future]] = future.result()
                st.success(f"Checked {len(uploaded_jobs)} job(s).")

            if "uploaded_job_logs" not in st.session_state:
                st.session_state["uploaded_job_logs"] = {}
            if "expanded_uploaded_jobs" not in st.session_state:
                st.session_state["expanded_uploaded_jobs"] = set()
            job_logs = st.session_state["uploaded_job_logs"]
            expanded_uploaded_jobs = st.session_state["expanded_uploaded_jobs"]

            for idx, (filename, job_id, password) in enumerate(uploaded_jobs):
                cached_status = job_statuses.get(job_id)
                with st.expander(
                    f"{filename} | Job ID: {job_id}" + (f" | {cached_status}" if cached_status else ""),
                    expanded=job_id in expanded_uploaded_jobs,
                ):
                    col_st, col_act = st.columns([2, 1])

                    with col_st:
                        st.write(f"**Password:** `{password}`")
                        if cached_status:
                            st.write(f"Last known status: **{cached_status}**")

                    with col_act:
                        if st.button(f"Check Status #{job_id}", key=f"uploaded_stat_{idx}"):
                            current_status = get_neos_status(job_id, password)
                            job_statuses[job_id] = current_status
                            cached_status = current_status
                            expanded_uploaded_jobs.add(job_id)
                            st.write(f"Current Status: **{current_status}**")

                        # getFinalResults blocks until the job finishes, so require a confirmed Done status first.
                        is_done = bool(cached_status) and "done" in cached_status.lower()
                        if is_done:
                            if st.button(f"Fetch Output Log #{job_id}", key=f"uploaded_res_{idx}"):
                                with st.spinner("Fetching output log from NEOS..."):
                                    job_logs[job_id] = get_neos_final_results(job_id, password)
                                expanded_uploaded_jobs.add(job_id)
                        else:
                            st.caption("Fetching is disabled until Check Status reports Done.")

                    if job_id in job_logs:
                        st.code(job_logs[job_id], language="text")
        else:
            st.warning("No Job ID and Password pairs were found in the uploaded text file.")
    
    if len(st.session_state["neos_jobs"]) == 0:
        st.info("No active NEOS jobs submitted during this session.")
    else:
        if "expanded_session_jobs" not in st.session_state:
            st.session_state["expanded_session_jobs"] = set()
        expanded_session_jobs = st.session_state["expanded_session_jobs"]

        for idx, job in enumerate(st.session_state["neos_jobs"]):
            job_filename = job.get("filename", "Unknown file")
            with st.expander(
                f"{job_filename} | Job ID: {job['id']} (Password: {job['password']})",
                expanded=idx in expanded_session_jobs,
            ):
                col_st, col_act = st.columns([2, 1])
                
                with col_st:
                    st.write(f"**Preview:** `{job['code']}`")
                
                with col_act:
                    if st.button(f"Check Status #{job['id']}", key=f"stat_{idx}"):
                        current_status = get_neos_status(job['id'], job['password'])
                        job['status'] = current_status
                        expanded_session_jobs.add(idx)
                        st.write(f"Current Status: **{current_status}**")

                    # getFinalResults blocks until the job finishes, so require a confirmed Done status first.
                    if "done" in str(job.get('status', '')).lower():
                        if st.button(f"Fetch Output Log #{job['id']}", key=f"res_{idx}"):
                            with st.spinner("Fetching output log from NEOS..."):
                                job["log"] = get_neos_final_results(job['id'], job['password'])
                            expanded_session_jobs.add(idx)
                    else:
                        st.caption("Fetching is disabled until Check Status reports Done.")

                if job.get("log"):
                    st.code(job["log"], language="text")
