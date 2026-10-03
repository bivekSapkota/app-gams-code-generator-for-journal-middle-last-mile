import io
import base64
import concurrent.futures
import datetime
import json
import math
import pathlib
import random
import re
import textwrap
import xmlrpc.client
import zipfile
import streamlit as st
import streamlit.components.v1 as components

_gams_editor_component = components.declare_component(
    "gams_editor", path=str(pathlib.Path(__file__).parent / "gams_editor")
)


IDE_THEMES = {
    "Dark (Monokai)": "monokai",
    "Dark (Dracula)": "dracula",
    "Dark (One Dark)": "one_dark",
    "Dark (Tomorrow Night)": "tomorrow_night",
    "Dark (Solarized)": "solarized_dark",
    "Colorful (Cobalt)": "cobalt",
    "Colorful (Twilight)": "twilight",
    "Light (GitHub)": "github",
    "Light (Xcode)": "xcode",
    "Light (Chrome)": "chrome",
    "Light (Solarized)": "solarized_light",
}


def gams_editor(value, height=500, key=None):
    """Ace-based editor with GAMS syntax highlighting; returns the current text."""
    result = _gams_editor_component(
        value=value, height=height, key=key, default=value,
        theme=IDE_THEMES.get(st.session_state.get("ide_theme"), "monokai"),
    )
    return value if result is None else result

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
    Generates deterministic compact master coordinates in miles.
    Typical node-to-node distances are about 3-4 miles; no master-table
    distance can exceed 25 miles.
    """
    geometry_seed = random.Random(42)
    hub_x, hub_y = 50, 50
    max_radius = 12.4

    def radial_coordinate(min_radius, max_coordinate_radius):
        angle = geometry_seed.uniform(0, 2 * math.pi)
        radius = geometry_seed.uniform(min_radius, max_coordinate_radius)
        return (
            round(hub_x + radius * math.cos(angle), 2),
            round(hub_y + radius * math.sin(angle), 2),
        )

    # A compact local core makes 3-4 mile links predominant. A small bounded
    # outer group adds geographic variation without permitting a >25-mile link.
    warehouses = {f"W{i+1}": radial_coordinate(1.9, 2.3) for i in range(5)}
    dcs = {f"DC{i+1}": radial_coordinate(1.9, 2.3) for i in range(15)}
    customers = {
        f"C{i+1}": radial_coordinate(1.9, 2.3) if i < 280 else radial_coordinate(8, max_radius)
        for i in range(300)
    }
    
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
    trans_cost_lm,
    trans_cost_mm,
    warehouse_inventory,
    dc_inventory,
    core_code=None,
    core_name="Efficiency Core",
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
    coordinates = {
        **{f"W{i}": master_w[f"W{i}"] for i in range(1, num_w + 1)},
        **{f"D{i}": master_dc[f"DC{i}"] for i in range(1, num_dc + 1)},
        **{f"C{i}": master_cust[f"C{i}"] for i in range(1, num_cust + 1)},
    }
    distance_rows = {
        row_node: [
            0 if row_node == column_node else max(
                1,
                calculate_euclidean_distance(coordinates[row_node], coordinates[column_node]),
            )
            for column_node in all_nodes
        ]
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

    gams_template = f"""$TITLE {core_name} - Journal Instance W{num_w} D{num_dc} C{num_cust}
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

Scalar TravelCostperTimeLM /{trans_cost_lm}/;
Scalar TravelCostperTimeMM /{trans_cost_mm}/;
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
TravellingCostEq.. TravelCost =e= sum((p,n,cd,cdp), TravelCostperTimeLM * (S(cd)+T(cd,cdp)) * x(p,n,cd,cdp)) + sum((p,wd,wdp), B(p,wd,wdp) * T(wd,wdp) * TravelCostperTimeMM);
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
    gams_code = textwrap.dedent(gams_template).strip()
    if core_code is None:
        return gams_code

    core_code = core_code.replace(
        "Scalar TravelCostperTimeLM   /2/;",
        f"Scalar TravelCostperTimeLM   /{trans_cost_lm}/;",
        1,
    ).replace(
        "Scalar TravelCostperTimeMM   /8/;",
        f"Scalar TravelCostperTimeMM   /{trans_cost_mm}/;",
        1,
    )
    data_preamble, _, _ = gams_code.partition("\nScalar NumOfCustomers;")
    if "Scalar TravelCostperTimeLM" in core_code and "Scalar TravelCostperTimeMM" in core_code:
        data_preamble = data_preamble.replace(
            f"Scalar TravelCostperTimeLM /{trans_cost_lm}/;\n", ""
        ).replace(
            f"Scalar TravelCostperTimeMM /{trans_cost_mm}/;\n", ""
        )
    return f"{data_preamble}\n\n{core_code.strip()}"

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

def visible_neos_output(log_text):
    summary_marker = re.search(r"REPORT SUMMARY\s*:", log_text)
    if summary_marker is None:
        return log_text
    return log_text[summary_marker.end():].lstrip()

def kill_neos_job(job_number, password):
    neos = get_neos_proxy(timeout=45)
    try:
        neos.killJob(job_number, password, "Terminated by user")
        return "Termination requested"
    except Exception as e:
        return f"Error terminating job: {str(e)}"

def safe_log_filename(filename, job_id):
    base = re.sub(r"[^\w.\-]+", "_", str(filename)).rsplit(".", 1)[0] or "job"
    return f"{base}_job{job_id}.log"

@st.dialog("Confirm termination")
def confirm_terminate_dialog(jobs, label):
    """jobs: list of (filename, job_id, password)."""
    st.warning(f"Are you sure you want to terminate {label} on the NEOS server? This cannot be undone.")
    col_yes, col_no = st.columns(2)
    with col_yes:
        if st.button("Yes, terminate", key="kill_confirm", use_container_width=True):
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                results = list(pool.map(lambda job: kill_neos_job(job[1], job[2]), jobs))
            failed = [r for r in results if r.startswith("Error")]
            for job, result in zip(jobs, results):
                record_history("terminate", filename=job[0], job_id=job[1], password=job[2], result=result)
            if failed:
                st.session_state["neos_kill_message"] = (
                    "warning",
                    f"{len(jobs) - len(failed)} terminated; {len(failed)} failed (e.g. already finished). {failed[0]}",
                )
            else:
                st.session_state["neos_kill_message"] = ("success", f"Termination requested for {len(jobs)} job(s).")
            st.rerun()
    with col_no:
        if st.button("Cancel", key="kill_cancel", use_container_width=True):
            st.rerun()

def render_bulk_neos_controls(jobs, logs, statuses, key_prefix, clear_jobs=None):
    """jobs: list of (filename, job_id, password); logs/statuses: dicts keyed by job_id, updated in place."""
    def is_done(job_id):
        return "done" in str(statuses.get(job_id) or "").lower()

    def build_zip():
        # Fetch any Done logs not yet retrieved so one click yields every available log.
        missing = [(j, p) for _, j, p in jobs if not logs.get(j) and is_done(j)]
        if missing:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                for (j, _), text in zip(missing, pool.map(lambda m: get_neos_final_results(m[0], m[1]), missing)):
                    logs[j] = text
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f, j, _ in jobs:
                if logs.get(j):
                    zf.writestr(safe_log_filename(f, j), logs[j])
        return buffer.getvalue()

    columns = st.columns(4 if clear_jobs is not None else 3)
    col_check, col_zip, col_kill = columns[:3]
    with col_check:
        if st.button("Check All Statuses", key=f"check_all_{key_prefix}", use_container_width=True):
            with st.spinner(f"Checking status for {len(jobs)} job(s)..."):
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                    futures = {pool.submit(get_neos_status, j, p): j for _, j, p in jobs}
                    for future in concurrent.futures.as_completed(futures):
                        statuses[futures[future]] = future.result()
            st.success(f"Checked {len(jobs)} job(s).")
    with col_zip:
        st.download_button(
            "Download All Logs (zip)",
            data=build_zip,
            file_name="neos_job_logs.zip",
            mime="application/zip",
            key=f"zip_all_{key_prefix}",
            disabled=not any(logs.get(j) or is_done(j) for _, j, _ in jobs),
            help="Enabled once a job is Done or has a fetched log.",
            use_container_width=True,
        )
    with col_kill:
        if st.button("Terminate All Jobs", key=f"kill_all_{key_prefix}", use_container_width=True):
            confirm_terminate_dialog(jobs, f"ALL {len(jobs)} job(s)")
    if clear_jobs is not None:
        with columns[3]:
            if st.button(
                "Clear All Jobs",
                key=f"clear_all_{key_prefix}",
                help="Remove all jobs from this view without terminating them or deleting history.",
                use_container_width=True,
            ):
                clear_jobs.clear()
                st.session_state["expanded_session_jobs"].clear()
                st.rerun()

# Plain-text JSON lines on disk (includes NEOS passwords); survives app restarts.
HISTORY_FILE = pathlib.Path(__file__).with_name("history.jsonl")

def record_history(event, **fields):
    entry = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

def load_history():
    if not HISTORY_FILE.exists():
        return []
    entries = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries

def restore_history_job(history_entry):
    job_id = history_entry.get("job_id")
    password = history_entry.get("password")
    if job_id is None or not password:
        return False

    jobs = st.session_state["neos_jobs"]
    job = next((item for item in jobs if str(item["id"]) == str(job_id)), None)
    if job is None:
        job = {
            "id": job_id,
            "password": password,
            "status": "Submitted",
            "filename": history_entry.get("filename", "Unknown file"),
            "code": "(restored from history)",
        }
        jobs.append(job)

    if "expanded_session_jobs" not in st.session_state:
        st.session_state["expanded_session_jobs"] = set()
    st.session_state["expanded_session_jobs"].add(job["id"])
    return True

def base_params():
    return {
        "driver_cost": s_dc_cost,
        "travel_cost_lm": s_trans_cost_lm,
        "travel_cost_mm": s_trans_cost_mm,
        "warehouse_inventory": s_warehouse_inventory,
        "dc_inventory": s_dc_inventory,
    }

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

JOURNAL_INEFFICIENT_CORRECTED_CORE = r"""*Scalar VehicleCapacity     /45/;
Scalar NumOfCustomers;
    NumOfCustomers = card(c);
scalar perwarehouseqty;
    perwarehouseqty = max(0, ceil(sum(c, E(c)) - sum(d, I(d))) / card(w));
    I(w) = perwarehouseqty;
Variable z;


Positive Variable
    Qdel(p,n,c)      delivered quantity to customer c
    Load(p,n,c)      load BEFORE serving customer c
    Q                 quantity loaded from depot d
    R(p,wd,wdp)      middle mile transfer quantity for period
    Inv(p, d)        Inventory of Distribution center at period p
    UsedTime(p,n)
    TravelCost
    DriverHiringCost
    DriversHired
    CPUTime, ElapsedTime

;

Binary Variable
    x   True when there is a last mile transfer DC to Customers at period p with driver n
    y(p,n)
    h(n)
    u(p,n,d)
    B True when the middle mile transfer occurs
;

* Constraint Descriptions
Equations
C1              objective function

C21             Each salesman can only have routing on a day if he is active that day
C22             Each salesman must be hired if he performs any routing over the horizon

C31             Each delivery point can have at most one incoming arc per day
C41             Each customer must have exactly one outgoing arc per day

C7              Flow conservation: inflow = outflow for each driver-day at each node
C8              Working time limit per driver-day
C9              Subtour elimination using MTZ ordering
C10             Used time definition
C11             Driver hiring cost definition

NoDCtoDC        Salesman cannot travel from one DC to another DC
StartDepotDef   Each active salesman selects exactly one DC per day
DepartFromDepot Each salesman must depart from exactly one DC
ReturnToDepot   Each salesman must return to the same DC

MeetTotalDemand Total delivered quantity must equal customer demand
LimitDelivery   Delivery allowed only if routing exists
XzeroIfQtyzero  No routing if delivered quantity is zero

*LoadCapUpper    Load before serving a customer cannot exceed vehicle capacity
LoadMinDemand   Load before serving a customer must be ≥ delivered quantity
LoadTransitionLB  Cumulative load consistency along the route lower bound
LoadTransitionUB  Cumulative load consistency along the route Upper bound

Q_Limit         Quantity loaded from DC allowed only if DC is selected
Q_LoadBalance   Total delivered = total loaded from DC
*DCInventory     DC inventory cannot be exceeded

TravellingCostEq Travel cost definition
NumDriversEq     Number of drivers hired
BTrueWhenFlow
NoSelfTravel

*These four constraints added for multiperiod middle mile transfer
*WarehouseInventoryP1 Initial inventory leads to Period 1 inventory
*WarehouseInventoryAfter Inventory at p-1 leads to inventory therafter
DCInventoryP1 Initial Inventory to Period 1 inventory
DCInventoryAfter P-1 inventory to Subsequent inventory
WarehouseTransfer Tranfers to dc from warehouse where inventory of WH is considered a very high number

;

* Model Constraints

C1.. z =e= DriverHiringCost + TravelCost;

C21(n,p).. sum((cd,cdp), x(p,n,cd,cdp)) =l= M*y(p,n);
C22(n)..   sum((p,cd,cdp), x(p,n,cd,cdp)) =l= M*h(n);

C31(p,cp).. sum((cd,n), x(p,n,cd,cp)) =l= 1;
C41(p,c)..  sum((cdp,n), x(p,n,c,cdp)) =l= 1;

C7(p,cd,n).. sum(cdp, x(p,n,cdp,cd)) =e= sum(cdp, x(p,n,cd,cdp));

C8(p,n).. sum((cd,cdp),(S(cdp)+T(cd,cdp))*x(p,n,cd,cdp)) =l= WorkingTime;

C9(p,n,c,cp).. ord(c)-ord(cp)+NumOfCustomers*x(p,n,c,cp) =l= NumOfCustomers-1;

C10(p,n).. UsedTime(p,n) =e= sum((cd,cdp),(S(cdp)+T(cd,cdp))*x(p,n,cd,cdp));

C11.. DriverHiringCost =e= sum((p,n),y(p,n))*DriverCostperPeriod;

NoDCtoDC(p,n).. sum((d,dp), x(p,n,d,dp)) =e= 0;

StartDepotDef(p,n).. sum(d, u(p,n,d)) =e= y(p,n);

DepartFromDepot(p,n,d).. sum(cdp, x(p,n,d,cdp)) =e= u(p,n,d);

ReturnToDepot(p,n,d).. sum(cdp, x(p,n,cdp,d)) =e= u(p,n,d);

MeetTotalDemand(c).. sum((p,n), Qdel(p,n,c)) =e= E(c);
*M below can be replaced by vehiclecapacity

LimitDelivery(p,n,c).. Qdel(p,n,c) =l= M*sum(cd, x(p,n,cd,c));

XzeroIfQtyzero(p,n,c).. sum(cd, x(p,n,cd,c)) =l= Qdel(p,n,c);

LoadMinDemand(p,n,c).. Load(p,n,c) =g= Qdel(p,n,c);

*M below (for both constraints) can be replaced by vehiclecapacity

LoadTransitionLB(p,n,c,cp).. Load(p,n,cp) =g= Load(p,n,c) - Qdel(p,n,c)- M * (1 - x(p,n,c,cp));

LoadTransitionUB(p,n,c,cp).. Load(p,n,cp) =l= Load(p,n,c) - Qdel(p,n,c)+ M * (1 - x(p,n,c,cp));

*M below can be replaced by vehiclecapacity

Q_Limit(p,n,d).. Q(p,n,d) =l= M* u(p,n,d);

Q_LoadBalance(p,n).. sum(c, Qdel(p,n,c)) =e= sum(d, Q(p,n,d));

*DCInventory(d)..  I(d)+ sum(w, R(w,d)) + sum(dp,R(dp,d)) -sum(dp,R(d,dp))- sum((p,n), Q(p,n,d)) =g= 0;

*WarehouseInventory(w).. I(w) + sum(wp,R(wp,w))-sum(wp,R(w,wp))- sum(d,R(w,d)) =g= 0;

BTrueWhenFlow(p, wd, wdp).. R(p, wd, wdp) =l= M * B(p, wd, wdp);

* Constraints for multi-period middle mile tranfer starts from here
*use these two warehouseInventory constraint if Inventory at warehouses considered finite.
*WarehouseInventoryP1(p, w)$(ord(p) = 1).. Inv(p, w) =e= I(w) - sum(wdp, R(p, w, wdp));

*WarehouseInventoryAfter(p, w)$(ord(p) > 1).. Inv(p, w) =e= Inv(p-1, w) + sum(wp, R(p-1, wp, w)) - sum(wdp, R(p, w, wdp));

*This constraint used for infinite qty assumption in warehouses
*WarehouseTransfer(p,w)..I(w) - sum(d,R(p,w,d)) =g= 0;

WarehouseTransfer(w)..I(w) - sum((p,d),R(p,w,d)) =g= 0;

DCInventoryP1(p,d)$(ord(p) = 1).. Inv(p, d) =e= I(d) - sum(dp, R(p, d, dp)) - sum(n, Q(p, n, d));

DCInventoryAfter(p, d)$(ord(p) > 1).. Inv(p, d) =e= Inv(p-1, d)
    + sum(wdp, R(p-1, wdp, d)) - sum(dp, R(p, d, dp)) - sum(n, Q(p, n, d));

NoSelfTravel(p,wd,wd).. B(p,wd,wd)=e=0;

TravellingCostEq.. TravelCost =e= sum((p,n,cd,cdp),TravelCostperTime * (S(cd)+T(cd,cdp)) * x(p,n,cd,cdp))
                    + sum((p,wd,wdp),B(p,wd,wdp) *T(wd,wdp)*TravelCostperTime);

NumDriversEq.. DriversHired =e= sum(n, h(n));

Model MTSP /ALL/;
Solve MTSP minimizing z using MIP;

CPUTime.l     = MTSP.resusd;
ElapsedTime.l = timeElapsed;

option x:0:0:1;
option Qdel:0:0:1;
option Load:0:0:1;
option Q:0:0:1;
option R:0:0:1;
option Inv:0:0:1;
option B:0:0:1

Display CPUTime.l, ElapsedTime.l, UsedTime.l;
Display x.l, Qdel.l,B.l,R.l, Q.l,Inv.l,perwarehouseqty TravelCost.l, DriverHiringCost.l, DriversHired.l;"""

JOURNAL_TRANSPORTATION_CORE = (
    JOURNAL_INEFFICIENT_CORRECTED_CORE
    .replace(
        "Scalar NumOfCustomers;",
        "Scalar TravelCostperTimeLM   /2/;\n"
        "Scalar TravelCostperTimeMM   /8/;\n"
        "Scalar NumOfCustomers;",
        1,
    )
    .replace(
        "TravelCostperTime * (S(cd)+T(cd,cdp))",
        "TravelCostperTimeLM * (S(cd)+T(cd,cdp))",
        1,
    )
    .replace(
        "T(wd,wdp)*TravelCostperTime",
        "T(wd,wdp)*TravelCostperTimeMM",
        1,
    )
)

JOURNAL_EFFICIENT_UNRESTRICTED_WAREHOUSE_CORE = r"""Scalar NumOfCustomers;
    NumOfCustomers = card(c);

Scalar TotalDemand;
    TotalDemand = sum(c, E(c));


Variable z;

Positive Variable
    Q(p,n,d)          quantity loaded from depot d
    R(p,wd,wdp)       middle mile transfer quantity for period
    Inv(p, wd)         Inventory of Distribution center at period p
    UsedTime(p,n)
    TravelCost
    DriverHiringCost
    DriversHired
    CPUTime, ElapsedTime
;

Binary Variable
    x                 True when there is a last mile transfer DC to Customers at period p with driver n
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
*WarehouseTransfer
    DCInventoryP1
    DCInventoryAfter
    WarehouseInventoryP1
    WarehouseInventoryAfter
    MaxDCTransfer
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

*use these two warehouseInventory constraint if Inventory at warehouses considered finite.
WarehouseInventoryP1(p, w)$(ord(p) = 1).. Inv(p, w) =e= I(w) - sum(wdp, R(p, w, wdp));
WarehouseInventoryAfter(p, w)$(ord(p) > 1).. Inv(p, w) =e= Inv(p-1, w) + sum(wp, R(p-1, wp, w)) - sum(wdp, R(p, w, wdp));
DCInventoryP1(p,d)$(ord(p) = 1).. Inv(p, d) =e= I(d) - sum(dp, R(p, d, dp)) - sum(n, Q(p, n, d));
DCInventoryAfter(p, d)$(ord(p) > 1).. Inv(p, d) =e= Inv(p-1, d) + sum(wdp, R(p-1, wdp, d)) - sum(dp, R(p, d, dp)) - sum(n, Q(p, n, d));
MaxDCTransfer.. sum((p, w, wd), R(p, w, wd)) =l= TotalDemand;
NoSelfTravel(p,wd,wd).. B(p,wd,wd) =e= 0;
TravellingCostEq.. TravelCost =e= sum((p,n,cd,cdp), TravelCostperTimeLM * (S(cd)+T(cd,cdp)) * x(p,n,cd,cdp)) + sum((p,wd,wdp), B(p,wd,wdp) * T(wd,wdp) * TravelCostperTimeMM);
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
Display x.l, B.l, R.l, Q.l, Inv.l, TravelCost.l, DriverHiringCost.l, DriversHired.l;"""

BUILT_IN_GAMS_CORES = {
    "Journal inefficient corrected": JOURNAL_INEFFICIENT_CORRECTED_CORE,
    "Journal Transportation": JOURNAL_TRANSPORTATION_CORE,
    "Efficient multiperiod Unrestricted Warehouse": JOURNAL_EFFICIENT_UNRESTRICTED_WAREHOUSE_CORE,
}

CUSTOM_CORES_FILE = pathlib.Path(__file__).with_name("custom_cores.json")

def load_custom_cores():
    if not CUSTOM_CORES_FILE.exists():
        return {}
    try:
        data = json.loads(CUSTOM_CORES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

def save_custom_cores(cores):
    CUSTOM_CORES_FILE.write_text(json.dumps(cores, indent=2), encoding="utf-8")

def select_gams_core(scope):
    st.subheader("GAMS Optimization Core")
    custom_cores = load_custom_cores()
    core_options = ["Efficiency Core", *BUILT_IN_GAMS_CORES, *custom_cores]
    choice_key = f"{scope}_core_choice"
    # A core deleted or renamed in the Cores tab must not linger as the selected value.
    if st.session_state.get(choice_key) not in core_options:
        st.session_state.pop(choice_key, None)
    core_choice = st.radio(
        "Core",
        core_options,
        index=core_options.index("Journal Transportation"),
        horizontal=True,
        key=choice_key,
    )
    if core_choice == "Efficiency Core":
        st.caption("Uses the current objectives, constraints, solve statement, and output displays.")
        return core_choice, None
    if core_choice in BUILT_IN_GAMS_CORES:
        st.caption("Uses the selected built-in GAMS core after Scalar M.")
        return core_choice, BUILT_IN_GAMS_CORES[core_choice]

    st.caption("Uses your saved custom core after Scalar M. Manage custom cores in the Cores tab.")
    return core_choice, custom_cores[core_choice].strip()

st.title("📦 GAMS Code Generator & Automated NEOS Runner")
st.caption(f"Developed by Bivek Sapkota | © {datetime.date.today().year} Bivek Sapkota. All rights reserved.")
st.markdown("Generate spatially consistent logistics network formulations backed by a **5 W / 15 DC / 300 Customer** benchmark coordinate grid.")

# --- SIDEBAR CONFIGURATION ---
st.sidebar.selectbox("🎨 Code editor theme", list(IDE_THEMES), key="ide_theme")

st.sidebar.header("🕹️ Baseline Parameter Settings")

s_num_w = st.sidebar.slider("Warehouses", 1, 5, 1)
s_num_dc = st.sidebar.slider("Distribution Centers", 1, 15, 2)
s_num_cust = st.sidebar.slider("Customers", 1, 300, 20)
s_num_periods = st.sidebar.slider("Planning Periods", 1, 5, 5)
s_num_drivers = st.sidebar.number_input("Driver Count", min_value=1, max_value=20, value=3)

st.sidebar.subheader("Cost Structure")
s_dc_cost = st.sidebar.number_input("Driver Cost per Period ($)", value=160)
s_trans_cost_lm = st.sidebar.number_input("Last-mile Travel Cost per Time", min_value=0, value=2, step=1)
s_trans_cost_mm = st.sidebar.number_input("Middle-mile Travel Cost per Time", min_value=0, value=8, step=1)

st.sidebar.subheader("Inventory Settings")
s_warehouse_inventory = st.sidebar.number_input("Warehouse Inventory", min_value=0, value=999, step=1)
s_dc_inventory = st.sidebar.number_input("DC Inventory", min_value=0, value=80, step=1)

# Main Application Tabs
tab_single, tab_batch, tab_cores, tab_neos, tab_history = st.tabs(
    ["📄 Single Model Generator", "📦 Batch Generator & Zip", "🧩 Cores", "🚀 NEOS Job View", "🕘 History"]
)

# ==========================================
# TAB 1: SINGLE MODEL GENERATION & MANUAL EDITOR
# ==========================================
with tab_single:
    col_ctrl, col_main = st.columns([1, 2])
    
    with col_ctrl:
        st.subheader("Model Synthesis")
        single_core_name, single_core_code = select_gams_core("single")
        if st.button("Generate GAMS Code", type="primary", use_container_width=True):
            if single_core_code == "":
                st.error("Enter a custom GAMS core before generating the model.")
            else:
                st.session_state["generated_code"] = compile_gams_code(
                    s_num_w, s_num_dc, s_num_cust, s_num_periods, s_num_drivers,
                    s_dc_cost, s_trans_cost_lm, s_trans_cost_mm,
                    s_warehouse_inventory, s_dc_inventory,
                    single_core_code,
                    single_core_name,
                )
                st.session_state["generated_core"] = single_core_name
                st.success(f"GAMS model compiled with the {single_core_name}!")
                record_history(
                    "generate_single",
                    core=single_core_name,
                    params={
                        "warehouses": s_num_w, "dcs": s_num_dc, "customers": s_num_cust,
                        "periods": s_num_periods, "drivers": s_num_drivers, **base_params(),
                    },
                )

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
                single_filename = f"{s_num_cust}C-{s_num_dc}DC-{s_num_w}WH-{s_num_periods}periods-{s_num_drivers}Drivers.GMS"
                record_history(
                    "submit_single",
                    filename=single_filename,
                    core=st.session_state.get("generated_core", ""),
                    email=user_email,
                    job_id=job_id,
                    password=pwd,
                    error=None if job_id else msg,
                    params={
                        "warehouses": s_num_w, "dcs": s_num_dc, "customers": s_num_cust,
                        "periods": s_num_periods, "drivers": s_num_drivers, **base_params(),
                    },
                )
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
            st.caption("Modify your GAMS model manually below:")
            edited_code = gams_editor(
                st.session_state["generated_code"],
                height=520,
                key="single_code_editor",
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
        batch_core_name, batch_core_code = select_gams_core("batch")

    if st.button("Generate Batch Package (.zip)", type="primary"):
        if batch_core_code == "":
            st.error("Enter a custom GAMS core before generating the batch package.")
        else:
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
                                        s_dc_cost, s_trans_cost_lm, s_trans_cost_mm,
                                        s_warehouse_inventory, s_dc_inventory,
                                        batch_core_code,
                                        batch_core_name,
                                    )
                                    core_suffix = re.sub(r"[^A-Za-z0-9]+", "", batch_core_name)
                                    filename = f"{c_val}C-{dc_val}DC-{w_val}WH-{p_val}periods-{d_val}Drivers-{core_suffix}.GMS"
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
                                        "core": batch_core_name,
                                        "params": {
                                            "warehouses": w_val, "dcs": dc_val, "customers": c_val,
                                            "periods": p_val, "drivers": d_val, **base_params(),
                                        },
                                    })
                                    zip_file.writestr(filename, code_str)

            zip_buffer.seek(0)
            record_history(
                "generate_batch",
                core=batch_core_name,
                file_count=batch_count,
                group_by=group_by,
                sweep={
                    "warehouses": sweep_warehouses, "dcs": sweep_dcs, "customers": sweep_customers,
                    "periods": sweep_periods, "drivers": sweep_drivers,
                },
                params=base_params(),
                files=[m["filename"] for m in st.session_state["batch_models"]],
            )
            st.success(
                f"Generated {batch_count} GAMS script files with the {batch_core_name}, "
                "maintaining spatial coordinate consistency!"
            )
            st.session_state["batch_zip_bytes"] = zip_buffer.getvalue()
            st.session_state["batch_zip_count"] = batch_count

    if st.session_state.get("batch_zip_bytes"):
        st.download_button(
            label=f"💾 Download All {st.session_state['batch_zip_count']} GAMS Files (.zip)",
            data=st.session_state["batch_zip_bytes"],
            file_name="gams_batch_experiments.zip",
            mime="application/zip",
            key="batch_zip_download",
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
    # Pre-fill new email slots as researcher1@ndsu.edu, researcher2@ndsu.edu, ...
    for i in range(int(num_emails)):
        st.session_state.setdefault(f"batch_email_{i}", f"researcher{i + 1}@ndsu.edu")
    batch_emails = [
        st.text_input(f"NEOS email #{i + 1} (files {i * NEOS_FILES_PER_EMAIL + 1}-{(i + 1) * NEOS_FILES_PER_EMAIL})",
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
                record_history(
                    "submit_batch",
                    filename=batch_model["filename"],
                    core=batch_model.get("core", ""),
                    email=submission_email,
                    job_id=job_id,
                    password=password,
                    error=None if job_id else message,
                    params=batch_model.get("params"),
                )
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

    if st.session_state.get("batch_credentials_report"):
        st.download_button(
            "Download NEOS credentials report",
            data=st.session_state["batch_credentials_report"],
            file_name="batch_neos_credentials.txt",
            mime="text/plain",
            key="batch_credentials_download",
        )

# ==========================================
# TAB: CORES (custom core management)
# ==========================================
with tab_cores:
    st.subheader("Custom GAMS Cores")
    st.caption(
        f"Saved to {CUSTOM_CORES_FILE.name} next to app.py, so cores persist across restarts and are "
        "available in the Single and Batch generators. A core is the GAMS code that follows Scalar M: "
        "declarations, objectives, constraints, model/solve statements, and outputs."
    )
    NEW_CORE = "➕ New core"
    saved_cores = load_custom_cores()
    if "pending_core_select" in st.session_state:
        st.session_state["core_manage_select"] = st.session_state.pop("pending_core_select")
    if st.session_state.get("core_manage_select") not in [NEW_CORE, *saved_cores]:
        st.session_state["core_manage_select"] = NEW_CORE
    selected_core = st.selectbox("Core to view or edit", [NEW_CORE, *saved_cores], key="core_manage_select")
    editing = selected_core != NEW_CORE
    reserved_names = {"Efficiency Core", *BUILT_IN_GAMS_CORES}

    new_core_name = st.text_input(
        "Core name",
        value=selected_core if editing else "",
        key=f"core_name_{selected_core}",
        placeholder="e.g. My Multiperiod Core",
    )
    new_core_code = gams_editor(
        saved_cores.get(selected_core, ""), height=420, key=f"core_editor_{selected_core}"
    )

    col_save, col_delete, _ = st.columns([1, 1, 4])
    if col_save.button("Save core", type="primary", use_container_width=True, key="core_save"):
        name = new_core_name.strip()
        code = new_core_code.strip()
        if not name:
            st.error("Enter a name for the core.")
        elif name in reserved_names:
            st.error(f'"{name}" is a built-in core name. Choose a different name.')
        elif not code:
            st.error("The core code is empty.")
        elif name != selected_core and name in saved_cores:
            st.error(f'A core named "{name}" already exists.')
        else:
            if editing and name != selected_core:
                saved_cores.pop(selected_core, None)
            saved_cores[name] = code
            save_custom_cores(saved_cores)
            st.session_state["pending_core_select"] = name
            st.rerun()
    if col_delete.button("Delete core", use_container_width=True, disabled=not editing, key="core_delete"):
        saved_cores.pop(selected_core, None)
        save_custom_cores(saved_cores)
        st.session_state["pending_core_select"] = NEW_CORE
        st.rerun()

# ==========================================
# TAB 3: NEOS JOB VIEW
# ==========================================
with tab_neos:
    st.subheader("NEOS Job View")
    st.caption(f"Submitted models use the CPLEX optimizer through {NEOS_ENDPOINT}.")

    # Buttons keyed "kill_*" are styled red.
    st.markdown(
        """<style>
        [class*="st-key-kill_"] button { background-color: #d32b2b; border-color: #d32b2b; color: white; }
        [class*="st-key-kill_"] button:hover { background-color: #a82020; border-color: #a82020; color: white; }
        </style>""",
        unsafe_allow_html=True,
    )
    kill_message = st.session_state.pop("neos_kill_message", None)
    if kill_message:
        getattr(st, kill_message[0])(kill_message[1])

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

            if "uploaded_job_logs" not in st.session_state:
                st.session_state["uploaded_job_logs"] = {}
            if "expanded_uploaded_jobs" not in st.session_state:
                st.session_state["expanded_uploaded_jobs"] = set()
            job_logs = st.session_state["uploaded_job_logs"]
            expanded_uploaded_jobs = st.session_state["expanded_uploaded_jobs"]

            render_bulk_neos_controls(uploaded_jobs, job_logs, job_statuses, "uploaded")

            for idx, (filename, job_id, password) in enumerate(uploaded_jobs):
                cached_status = job_statuses.get(job_id)
                col_exp, col_kill_btn = st.columns([6, 1])
                with col_kill_btn:
                    if st.button("Terminate", key=f"kill_uploaded_{idx}", use_container_width=True):
                        confirm_terminate_dialog([(filename, job_id, password)], f"job #{job_id}")
                with col_exp, st.expander(
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
                        st.download_button(
                            f"Download Log #{job_id}",
                            data=job_logs[job_id],
                            file_name=safe_log_filename(filename, job_id),
                            mime="text/plain",
                            key=f"uploaded_dl_{idx}",
                        )
                        st.code(visible_neos_output(job_logs[job_id]), language="text")
        else:
            st.warning("No Job ID and Password pairs were found in the uploaded text file.")
    
    if len(st.session_state["neos_jobs"]) == 0:
        st.info("No active NEOS jobs submitted during this session.")
    else:
        if "expanded_session_jobs" not in st.session_state:
            st.session_state["expanded_session_jobs"] = set()
        expanded_session_jobs = st.session_state["expanded_session_jobs"]

        session_jobs = st.session_state["neos_jobs"]
        session_statuses = {j["id"]: j.get("status") for j in session_jobs}
        session_logs = {j["id"]: j.get("log") for j in session_jobs}
        render_bulk_neos_controls(
            [(j.get("filename", "job"), j["id"], j["password"]) for j in session_jobs],
            session_logs,
            session_statuses,
            "session",
            clear_jobs=session_jobs,
        )
        for j in session_jobs:
            j["status"] = session_statuses.get(j["id"])
            j["log"] = session_logs.get(j["id"])

        for idx, job in enumerate(st.session_state["neos_jobs"]):
            job_filename = job.get("filename", "Unknown file")
            col_exp, col_clear_btn, col_kill_btn = st.columns([5, 1, 1])
            with col_clear_btn:
                if st.button(
                    "Clear",
                    key=f"clear_session_{job['id']}",
                    help="Remove this job from the view without terminating it or deleting history.",
                    use_container_width=True,
                ):
                    st.session_state["neos_jobs"] = [
                        item for item in st.session_state["neos_jobs"]
                        if str(item["id"]) != str(job["id"])
                    ]
                    expanded_session_jobs.discard(job["id"])
                    st.rerun()
            with col_kill_btn:
                if st.button("Terminate", key=f"kill_session_{idx}", use_container_width=True):
                    confirm_terminate_dialog([(job_filename, job["id"], job["password"])], f"job #{job['id']}")
            with col_exp, st.expander(
                f"{job_filename} | Job ID: {job['id']} (Password: {job['password']})"
                + (f" | Status: {job['status']}" if job.get("status") else ""),
                expanded=job["id"] in expanded_session_jobs,
            ):
                col_st, col_act = st.columns([2, 1])
                
                with col_st:
                    st.write(f"**Preview:** `{job['code']}`")
                
                with col_act:
                    if st.button(f"Check Status #{job['id']}", key=f"stat_{idx}"):
                        current_status = get_neos_status(job['id'], job['password'])
                        job['status'] = current_status
                        expanded_session_jobs.add(job["id"])
                        st.write(f"Current Status: **{current_status}**")

                    # getFinalResults blocks until the job finishes, so require a confirmed Done status first.
                    if "done" in str(job.get('status', '')).lower():
                        if st.button(f"Fetch Output Log #{job['id']}", key=f"res_{idx}"):
                            with st.spinner("Fetching output log from NEOS..."):
                                job["log"] = get_neos_final_results(job['id'], job['password'])
                            expanded_session_jobs.add(job["id"])
                    else:
                        st.caption("Fetching is disabled until Check Status reports Done.")

                if job.get("log"):
                    st.download_button(
                        f"Download Log #{job['id']}",
                        data=job["log"],
                        file_name=safe_log_filename(job_filename, job['id']),
                        mime="text/plain",
                        key=f"dl_{idx}",
                    )
                    st.code(visible_neos_output(job["log"]), language="text")

# ==========================================
# TAB 4: PERSISTENT HISTORY
# ==========================================
with tab_history:
    st.subheader("Activity History")
    st.caption(f"Saved to {HISTORY_FILE.name} next to app.py, so it persists across restarts. It contains NEOS passwords in plain text.")
    history_message = st.session_state.pop("neos_history_message", None)
    if history_message:
        st.success(history_message)
    history = load_history()
    if not history:
        st.info("No activity recorded yet.")
    else:
        event_filter = st.multiselect(
            "Show events",
            sorted({h["event"] for h in history}),
            default=sorted({h["event"] for h in history}),
        )
        visible_history = [h for h in reversed(history) if h["event"] in event_filter]

        # Terminate entries carry no password, so look it up from the matching submission.
        job_passwords = {
            str(h["job_id"]): h["password"]
            for h in history
            if h.get("job_id") is not None and h.get("password")
        }

        def format_params(params):
            if not params:
                return ""
            if isinstance(params, dict):
                return "{" + ", ".join(f"{k}: {v}" for k, v in params.items()) + "}"
            return json.dumps(params)

        # Terminate entries and older submissions lack a core, so use the submission's own core,
        # falling back to the core of the most recent generate event before it.
        job_cores = {}
        last_generated_core = ""
        for h in history:
            if h["event"] in ("generate_single", "generate_batch"):
                last_generated_core = h.get("core", "")
            elif h["event"] in ("submit_single", "submit_batch") and h.get("job_id") is not None:
                job_cores[str(h["job_id"])] = h.get("core") or last_generated_core

        # Terminate entries carry no parameters, so reuse those of the matching submission.
        job_params = {
            str(h["job_id"]): h.get("params") or h.get("sweep")
            for h in history
            if h.get("job_id") is not None and (h.get("params") or h.get("sweep"))
        }

        st.markdown(
            """<style>
            [class*="st-key-history_job_"] button {
                background-color: #1f9d3a; border-color: #1f9d3a; color: white;
                min-height: 1.5rem; height: 1.5rem; padding: 0 0.5rem; white-space: nowrap;
            }
            [class*="st-key-history_job_"] button p { font-size: 0.7rem; line-height: 1; }
            [class*="st-key-history_job_"] button:hover { background-color: #167a2c; border-color: #167a2c; color: white; }
            [class*="st-key-history_table"] { overflow-x: auto; gap: 0; }
            [class*="st-key-history_row_"], [class*="st-key-history_head"] {
                border: 1px solid #e3e6ea; border-top: none; padding: 0; gap: 0; min-width: 1500px;
            }
            [class*="st-key-history_head"] { border-top: 1px solid #e3e6ea; background-color: #f4f6f8; }
            [class*="st-key-history_row_"]:nth-of-type(even) { background-color: #fafbfc; }
            [class*="st-key-history_row_"] [data-testid="stHorizontalBlock"],
            [class*="st-key-history_head"] [data-testid="stHorizontalBlock"] {
                gap: 0; align-items: stretch !important;
            }
            [class*="st-key-history_row_"] [data-testid="stColumn"],
            [class*="st-key-history_head"] [data-testid="stColumn"] {
                border-right: 1px solid #e3e6ea; padding: 6px 8px; min-width: 0; overflow-wrap: anywhere;
                display: flex; flex-direction: column; justify-content: center;
            }
            [class*="st-key-history_row_"] [data-testid="stColumn"]:last-child,
            [class*="st-key-history_head"] [data-testid="stColumn"]:last-child { border-right: none; }
            [class*="st-key-history_row_"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"],
            [class*="st-key-history_head"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {
                flex: 0 0 auto; width: 100%; gap: 0; height: auto !important; min-height: fit-content;
            }
            [class*="st-key-history_row_"] [data-testid="stElementContainer"],
            [class*="st-key-history_head"] [data-testid="stElementContainer"] {
                height: auto !important; min-height: fit-content; flex-shrink: 0;
            }
            [class*="st-key-history_row_"] p, [class*="st-key-history_head"] p {
                margin: 0; font-size: 0.72rem; line-height: 1.35; min-height: 0;
            }
            [class*="st-key-history_head"] p { font-weight: 600; }
            [class*="st-key-history_row_"] [data-testid="stMarkdownContainer"],
            [class*="st-key-history_head"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
            [class*="st-key-history_row_"] [data-testid="stMarkdown"],
            [class*="st-key-history_head"] [data-testid="stMarkdown"],
            [class*="st-key-history_row_"] [data-testid="stMarkdown"] > div,
            [class*="st-key-history_head"] [data-testid="stMarkdown"] > div { height: auto !important; min-height: fit-content; }
            [class*="st-key-history_row_"] [data-testid="stElementContainer"],
            [class*="st-key-history_head"] [data-testid="stElementContainer"] { width: 100%; }
            </style>""",
            unsafe_allow_html=True,
        )
        max_rows = st.number_input("Rows to show (most recent first)", min_value=10, max_value=2000, value=100, step=50)
        st.caption("Click a green Job ID to load that job into the NEOS Job View.")

        column_widths = [2, 1.5, 3, 2, 3, 2, 2.5, 3, 6]
        with st.container(key="history_table"):
            with st.container(key="history_head"):
                header_cols = st.columns(column_widths)
                titles = ["Time", "Event", "File", "Core", "Email", "Job ID", "Password", "Result", "Parameters"]
                for col, title in zip(header_cols, titles):
                    col.write(title)

            for index, h in enumerate(visible_history[:int(max_rows)]):
                with st.container(key=f"history_row_{index}"):
                    cols = st.columns(column_widths)
                    cols[0].write(h["time"])
                    cols[1].write(h["event"])
                    cols[2].write(h.get("filename", ""))
                    cols[3].write(
                        h.get("core") or (job_cores.get(str(h["job_id"]), "") if h.get("job_id") is not None else "")
                    )
                    cols[4].write(h.get("email", ""))
                    job_id = h.get("job_id")
                    password = h.get("password") or (job_passwords.get(str(job_id)) if job_id is not None else None)
                    if job_id is not None and password:
                        if cols[5].button(
                            str(job_id),
                            key=f"history_job_{index}",
                            help=f"Load {h.get('filename') or 'this job'} into the NEOS Job View.",
                            use_container_width=True,
                        ):
                            restore_history_job({**h, "password": password})
                            st.session_state["neos_history_message"] = (
                                f"Job #{job_id} is now available in the NEOS Job View."
                            )
                            st.rerun()
                    else:
                        cols[5].write(job_id if job_id is not None else "")
                    cols[6].write(h.get("password", ""))
                    cols[7].write(h.get("error") or h.get("result") or "")
                    params = h.get("params") or h.get("sweep")
                    if not params and job_id is not None:
                        params = job_params.get(str(job_id))
                    cols[8].write(format_params(params))

        known_ids = {str(job["id"]) for job in st.session_state["neos_jobs"]}
        restorable = []
        seen_restorable_ids = set()
        for entry in reversed(history):
            job_id = entry.get("job_id")
            if (
                entry["event"] in ("submit_single", "submit_batch")
                and job_id is not None
                and entry.get("password")
                and str(job_id) not in known_ids
                and str(job_id) not in seen_restorable_ids
            ):
                restorable.append(entry)
                seen_restorable_ids.add(str(job_id))
        if st.button(f"Load {len(restorable)} past job(s) into NEOS Job View", disabled=not restorable):
            for entry in restorable:
                restore_history_job(entry)
            st.session_state["neos_history_message"] = (
                f"Loaded {len(restorable)} past job(s) into the NEOS Job View."
            )
            st.rerun()
