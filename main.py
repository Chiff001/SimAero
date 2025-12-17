from collections import defaultdict
import matplotlib.pyplot as plt
import simpy
import random
from air_logger import AirLogger
import time
import streamlit as st
import pandas as pd


st.set_page_config(page_title="Симуляция сборочной линии", layout="wide")

st.title("Симуляция сборочной линии самолетов")
st.markdown("---")

# Создаем сайдбар для ввода параметров
with st.sidebar:
    st.header("Параметры модели")
    
    with st.form("model_parameters"):
        # Основные параметры
        st.subheader("Основные параметры")
        NUM_STATIONS = st.slider("Количество рабочих участков", 3, 10, 5, 1)
        MECHANICS_PER_SHIFT = st.slider("Механиков в смену", 1, 20, 8, 1)
        PLANE_INTERVAL = st.slider("Интервал поступления самолетов (дни)", 1, 10, 4, 1)
        SIM_TIME = st.slider("Время симуляции (дни)", 7, 90, 45, 1)
        MAX_PLAIN_PER_STATION = st.slider("Макс. самолетов на станции", 1, 3, 1, 1)
        
        # Распределение задач
        st.subheader("Распределение задач")
        st.write("Количество задач на участке:")
        col1, col2 = st.columns(2)
        with col1:
            min_tasks = st.number_input("Минимум", 1, 50, 5, 1)
        with col2:
            max_tasks = st.number_input("Максимум", 10, 100, 20, 1)
        
        # Вероятность типов самолетов
        st.subheader("Типы самолетов")
        sa101_prob = st.slider("Вероятность SA101", 0.0, 1.0, 0.6, 0.05)
        
        # Рабочие смены
        st.subheader("Рабочие смены")
        shifts = []
        for i in range(4):
            col1, col2 = st.columns(2)
            with col1:
                start = st.number_input(f"Начало {i+1}", 0.0, 24.0, [6.0, 10.5, 14.5, 19.0][i], 0.5)
            with col2:
                end = st.number_input(f"Конец {i+1}", 0.0, 24.0, [10.0, 14.5, 18.5, 22.0][i], 0.5)
            shifts.append((start, end))
        SHIFT_HOURS = shifts
        
        # Кнопка запуска
        submitted = st.form_submit_button("Запустить симуляцию")

# Объявление логеров
logger_line = AirLogger(".print", "level1", "level2", prefix="plane")
logger_area = AirLogger("level1", "level2", prefix="area")
logger_task = AirLogger("level2", prefix="task")
logger_planes = {}

# Генерация задач на основе выбранных параметров
TASKS_PER_STATION_SA101 = [random.randint(min_tasks, max_tasks) for _ in range(NUM_STATIONS)]
TASKS_PER_STATION_SA102 = [random.randint(min_tasks, max_tasks) for _ in range(NUM_STATIONS)]

# Переменные для сбора данных
station_work_times = defaultdict(list)
plane_wait_times = []
plane_queue_counts = []

planes_arrived = 0
planes_completed = 0

active_mechanics = 0
mechanics_usage = []

# Метрики в зависимости от типа самолета
station_work_times_by_type = defaultdict(lambda: defaultdict(list))
plane_wait_times_by_type = defaultdict(list)
plane_queue_counts_by_type = defaultdict(list)
planes_arrived_by_type = defaultdict(int)
planes_completed_by_type = defaultdict(int)

class Plane:
    """Класс для представления самолета."""

    def __init__(self, plane_id, plane_type):
        self.plane_id = plane_id
        self.plane_type = plane_type

        # Количество задач на станциях зависит от типа самолета
        if plane_type == "SA101":
            self.tasks_per_station = TASKS_PER_STATION_SA101
        if plane_type == "SA102":
            self.tasks_per_station = TASKS_PER_STATION_SA102

def task_time():
    return random.uniform(1, 2)

def generate_task_dependencies(num_tasks):
    task_sequences = [random.randint(1, 3) for _ in range(num_tasks)]
    dependencies = {task_id: [] for task_id in range(num_tasks)}
    for task_id, seq in enumerate(task_sequences):
        dependencies[task_id] = [prev_id for prev_id, prev_seq in enumerate(task_sequences) if prev_seq < seq]
    return dependencies, task_sequences

class Mechanic:
    def __init__(self, env, station_id):
        self.env = env
        self.station_id = station_id
        self.shift_active = False
        self.working = False

    def work_shift(self, start, end):
        global active_mechanics
        while True:
            self.shift_active = True
            yield self.env.timeout(end - start)
            self.shift_active = False
            yield self.env.timeout(24 - (end - start))

    def perform_task(self, task_duration):
        """Механик начинает работать над задачей"""
        global active_mechanics
        self.working = True
        active_mechanics += 1
        yield self.env.timeout(task_duration)
        self.working = False
        active_mechanics -= 1

class AssemblyLine:
    def __init__(self, env):
        self.env = env
        self.stations = [simpy.Resource(env, capacity=MAX_PLAIN_PER_STATION) for _ in range(NUM_STATIONS)]
        self.mechanics = {i: [Mechanic(env, i) for _ in range(MECHANICS_PER_SHIFT)] for i in range(NUM_STATIONS)}

        # Инициализация смен для механиков
        for station_id, mechanics in self.mechanics.items():
            for mechanic in mechanics:
                env.process(self.run_mechanic_shifts(mechanic))

    def run_mechanic_shifts(self, mechanic):
        for start, end in SHIFT_HOURS:
            yield self.env.process(mechanic.work_shift(start, end))

    def get_available_mechanic(self, station_id):
        available_mechanics = [m for m in self.mechanics[station_id] if m.shift_active]
        if available_mechanics:
            return random.choice(available_mechanics)
        return None

    def process_plane(self, plane):
        global planes_completed

        for i, station in enumerate(self.stations):
            start_time = self.env.now
            with station.request() as request:
                wait_start = self.env.now
                plane_queue_counts_by_type[plane.plane_type].append((self.env.now, len(station.queue)))
                yield request
                
                wait_end = self.env.now
                plane_wait_times_by_type[plane.plane_type].append((self.env.now, wait_end - wait_start))

                # Выполнение задач
                dependencies, sequences = generate_task_dependencies(plane.tasks_per_station[i])
                tasks_status = {task_id: False for task_id in range(plane.tasks_per_station[i])}

                while not all(tasks_status.values()):
                    available_tasks = [task_id for task_id, done in tasks_status.items() if
                                       not done and all(tasks_status[dep] for dep in dependencies[task_id])]
                    for task_id in available_tasks:
                        mechanic = self.get_available_mechanic(i)
                        while not mechanic:
                            yield self.env.timeout(1)
                            mechanic = self.get_available_mechanic(i)

                        task_duration = task_time()
                        self.env.process(mechanic.perform_task(task_duration))
                        yield self.env.timeout(task_duration)
                        tasks_status[task_id] = True

            end_time = self.env.now
            station_work_times_by_type[plane.plane_type][i].append(end_time - start_time)

            if i < len(self.stations) - 1:
                yield self.env.timeout(0.5)

        planes_completed_by_type[plane.plane_type] += 1
        planes_completed += 1

    def add_plane(self, env):
        global planes_arrived
        plane_id = 1
        while True:
            plane_type = "SA101" if random.random() < sa101_prob else "SA102"
            plane = Plane(plane_id, plane_type)

            planes_arrived += 1
            planes_arrived_by_type[plane_type] += 1

            env.process(self.process_plane(plane))
            plane_id += 1
            yield env.timeout(PLANE_INTERVAL * 24)

def record_mechanics_usage(env):
    """Записываем количество задействованных механиков на протяжении времени"""
    while True:
        mechanics_usage.append((env.now, active_mechanics))
        yield env.timeout(1)

def model_env():
    if not submitted:
        st.info("Задайте параметры в сайдбаре и нажмите 'Запустить симуляцию'")
        return
        
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    env = simpy.Environment()
    assembly_line = AssemblyLine(env)
    env.process(assembly_line.add_plane(env))
    env.process(record_mechanics_usage(env))

    status_text.text("Запуск симуляции производственного процесса...")
    
    # Запуск симуляции
    for i in range(100):
        env.run(until=SIM_TIME * 24 * (i+1) / 100)
        progress_bar.progress((i+1) / 100)
    
    status_text.text("Симуляция завершена!")
    
    # ================ ОТОБРАЖЕНИЕ РЕЗУЛЬТАТОВ ================
    st.header("Результаты симуляции")
    
    # Сводная статистика
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Всего самолетов поступило", planes_arrived)
        st.metric("SA101 поступило", planes_arrived_by_type.get("SA101", 0))
        st.metric("SA102 поступило", planes_arrived_by_type.get("SA102", 0))
    
    with col2:
        st.metric("Всего собрано", planes_completed)
        st.metric("SA101 собрано", planes_completed_by_type.get("SA101", 0))
        st.metric("SA102 собрано", planes_completed_by_type.get("SA102", 0))
    
    with col3:
        if planes_arrived > 0:
            efficiency = (planes_completed / planes_arrived) * 100
            st.metric("Эффективность", f"{efficiency:.1f}%")
    
    # Графики
    st.subheader("Визуализация результатов")
    
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Самолеты", "Время работы", "Ожидание", "Очередь", "Механики"
    ])
    
    with tab1:
        fig, ax = plt.subplots()
        labels = list(planes_arrived_by_type.keys())
        x = range(len(labels))
        
        arrived = [planes_arrived_by_type.get(label, 0) for label in labels]
        completed = [planes_completed_by_type.get(label, 0) for label in labels]
        
        bar_width = 0.4
        ax.bar(x, arrived, width=bar_width, label="Поступили", color="blue", align="center")
        ax.bar(x, completed, width=bar_width, label="Собраны", color="green", align="edge")
        
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlabel("Тип самолета")
        ax.set_ylabel("Количество самолетов")
        ax.set_title("Количество поступивших и собранных самолетов по типам")
        ax.legend()
        st.pyplot(fig)
    
    with tab2:
        fig, ax = plt.subplots()
        avg_work_times_by_type = {}
        for plane_type, station_data in station_work_times_by_type.items():
            avg_work_times_by_type[plane_type] = {station: sum(times) / len(times) if times else 0
                                                  for station, times in station_data.items()}
        
        x_labels = sorted({station for avg_work_times in avg_work_times_by_type.values() 
                          for station in avg_work_times.keys()})
        x = range(len(x_labels))
        
        bar_width = 0.4
        offset = -bar_width / 2 * len(avg_work_times_by_type)
        
        for idx, (plane_type, avg_work_times) in enumerate(avg_work_times_by_type.items()):
            positions = [pos + offset + idx * bar_width for pos in x]
            values = [avg_work_times.get(station, 0) for station in x_labels]
            ax.bar(positions, values, width=bar_width, alpha=0.7, label=f"{plane_type}")
        
        ax.set_xticks(x)
        ax.set_xticklabels([f"Станция {i+1}" for i in x_labels])
        ax.set_xlabel("Станции")
        ax.set_ylabel("Среднее время работы (часы)")
        ax.set_title("Среднее время работы на станциях по типам самолетов")
        ax.legend()
        st.pyplot(fig)
    
    with tab3:
        fig, ax = plt.subplots()
        for plane_type, times_and_waits in plane_wait_times_by_type.items():
            if times_and_waits:
                times, waits = zip(*times_and_waits)
                ax.plot(times, waits, marker="o", label=f"{plane_type}")
        
        ax.set_xlabel("Время (часы)")
        ax.set_ylabel("Время ожидания (часы)")
        ax.set_title("Время ожидания самолетов от времени по типам")
        ax.legend()
        st.pyplot(fig)
    
    with tab4:
        fig, ax = plt.subplots()
        aggregated_queue_counts = defaultdict(lambda: defaultdict(int))
        time_interval = 24
        
        for plane_type, times_and_counts in plane_queue_counts_by_type.items():
            for time, count in times_and_counts:
                aggregated_time = int(time // time_interval) * time_interval
                aggregated_queue_counts[aggregated_time][plane_type] += count
        
        if aggregated_queue_counts:
            times = sorted(aggregated_queue_counts.keys())
            plane_types = list(plane_queue_counts_by_type.keys())
            plane_counts_by_time = {plane_type: [0] * len(times) for plane_type in plane_types}
            
            for idx, time in enumerate(times):
                for plane_type in plane_types:
                    plane_counts_by_time[plane_type][idx] = aggregated_queue_counts[time].get(plane_type, 0)
            
            bar_width = 0.35
            positions = range(len(times))
            
            for idx, plane_type in enumerate(plane_types):
                ax.bar([p + idx * bar_width for p in positions],
                      plane_counts_by_time[plane_type],
                      width=bar_width,
                      label=f"{plane_type}")
            
            ax.set_xticks([p + bar_width for p in positions])
            ax.set_xticklabels([f"День {t // time_interval + 1}" for t in times], rotation=45)
            ax.set_xlabel("День")
            ax.set_ylabel("Количество ожидающих самолетов")
            ax.set_title("Количество ожидающих самолетов по типам")
            ax.legend()
            ax.grid(axis="y", linestyle="--", alpha=0.7)
        st.pyplot(fig)
    
    with tab5:
        col1, col2 = st.columns(2)
        
        with col1:
            fig1, ax1 = plt.subplots()
            if mechanics_usage:
                times, usage = zip(*mechanics_usage)
                ax1.bar(times[:100], usage[:100], color="orange", width=0.8)
                ax1.set_xlabel("Время (часы)")
                ax1.set_ylabel("Количество активных механиков")
                ax1.set_title("Активные механики (первые 100 часов)")
                ax1.grid(axis="y", linestyle="--", alpha=0.7)
            st.pyplot(fig1)
        
        with col2:
            fig2, ax2 = plt.subplots()
            max_daily_usage = defaultdict(list)
            for time, count in mechanics_usage:
                day = int(time // 24)
                max_daily_usage[day].append(count)
            
            max_daily_usage = {day: max(counts) for day, counts in max_daily_usage.items()}
            
            if max_daily_usage:
                days = sorted(max_daily_usage.keys())
                max_usages = [max_daily_usage[day] for day in days]
                ax2.plot(days, max_usages, marker="o", linestyle="-", color="b", 
                        label="Макс. использование механиков")
                ax2.set_xlabel("Дни")
                ax2.set_ylabel("Максимальное количество механиков")
                ax2.set_title("Максимальное использование механиков по дням")
                ax2.grid(True, linestyle="--", alpha=0.7)
                ax2.legend()
            st.pyplot(fig2)
    
    # Детальная таблица данных
    st.subheader("Детальная статистика")
    
    # Создаем DataFrame с результатами
    data = []
    for plane_type in ["SA101", "SA102"]:
        data.append({
            "Тип самолета": plane_type,
            "Поступило": planes_arrived_by_type.get(plane_type, 0),
            "Собрано": planes_completed_by_type.get(plane_type, 0),
            "Эффективность": f"{(planes_completed_by_type.get(plane_type, 0) / planes_arrived_by_type.get(plane_type, 1) * 100):.1f}%" if planes_arrived_by_type.get(plane_type, 0) > 0 else "0%"
        })
    
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True)

# Запуск модели
if __name__ == "__main__":
    model_env()