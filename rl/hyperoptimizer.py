import multiprocessing as mp
from hyperopt import fmin, tpe, hp, STATUS_OK, Trials
from rl.util import *


class HyperOptimizer(object):

    '''
    The base class of hyperparam optimizer, with core methods
    '''

    def __init__(self, Trial, **kwargs):
        self.REQUIRED_GLOBAL_VARS = [
            'experiment_spec',
            'times'
        ]
        self.check_set_keys(**kwargs)
        self.run_timestamp = timestamp()
        self.Trial = Trial
        self.generate_param_space()

    def check_set_keys(self, **kwargs):
        assert all(k in kwargs for k in self.REQUIRED_GLOBAL_VARS)
        for k in kwargs:
            setattr(self, k, kwargs[k])

    def generate_param_space(self):
        raise NotImplementedError()

    def run(self):
        raise NotImplementedError()


class HyperoptHyperOptimizer(HyperOptimizer):

    def check_set_keys(self, **kwargs):
        self.REQUIRED_GLOBAL_VARS = [
            'experiment_spec',
            'times',
            'max_evals'
        ]
        raw_experiment_spec = kwargs.pop('experiment_spec')
        assert 'param' in raw_experiment_spec
        assert 'param_range' in raw_experiment_spec
        self.common_experiment_spec = copy.deepcopy(raw_experiment_spec)
        self.common_experiment_spec.pop('param')
        self.common_experiment_spec.pop('param_range')
        self.default_param = raw_experiment_spec['param']
        self.param_range = raw_experiment_spec['param_range']
        self.trial_num = 0
        self.algo = tpe.suggest

        super(HyperoptHyperOptimizer, self).check_set_keys(**kwargs)

    def convert_to_hp(self, k, v):
        '''
        convert to hyperopt param expressions. refer:
        https://github.com/hyperopt/hyperopt/wiki/FMin#21-parameter-expressions
        param = {
            'learning_rate': {
                'uniform': {
                    'low': 0.0001,
                    'high': 1.0
                }
            },
            'hidden_layers_activation': ['relu', 'linear']
        }
        for k in param:
            v = param[k]
            print(convert_to_hp(k, v))
        '''
        if isinstance(v, list):
            return hp.choice(k, v)
        elif isinstance(v, dict):
            space_keys = list(v.keys())
            assert len(space_keys) == 1
            space_k = space_keys[0]
            space_v = v[space_k]
            space = getattr(hp, space_k)(k, **space_v)
            return space
        else:
            raise TypeError(
                'experiment_spec param_range value must be a list or dict')

    # generate param_space for hyperopt from experiment_spec
    def generate_param_space(self):
        self.param_space = copy.copy(self.default_param)
        for k in self.param_range:
            v = self.param_range[k]
            space = self.convert_to_hp(k, v)
            self.param_space[k] = space
        return self.param_space

    def increment_var(self):
        self.trial_num += 1

    def get_next_var(self):
        self.increment_var()
        return self.__dict__

    def hyperopt_run_trial(self, param):
        # use param to carry those params other than experiment_spec
        # set a global gvs: global variable source
        gv = self.get_next_var()
        experiment_spec = gv['common_experiment_spec']
        experiment_spec.update({'param': param})

        trial = self.Trial(
            experiment_spec,
            times=gv['times'],
            trial_num=gv['trial_num'],
            num_of_trials=gv['max_evals'],
            run_timestamp=gv['run_timestamp'])
        trial_data = trial.run()
        metrics = trial_data['summary']['metrics']
        # to maximize avg mean rewards/epi via minimization
        hyperopt_loss = -1. * metrics['mean_rewards_per_epi_stats'][
            'mean'] / trial_data['sys_vars_array'][0][
            'SOLVED_MEAN_REWARD']
        return {'loss': hyperopt_loss,
                'status': STATUS_OK,
                'trial_data': trial_data}

    def run(self):
        trials = Trials()
        fmin(fn=self.hyperopt_run_trial,
             space=self.param_space,
             algo=self.algo,
             max_evals=self.max_evals,
             trials=trials)
        experiment_data = [
            trial['result']['trial_data'] for trial in trials]
        return experiment_data


class BruteHyperOptimizer(HyperOptimizer):

    def check_set_keys(self, **kwargs):
        self.REQUIRED_GLOBAL_VARS = [
            'experiment_spec',
            'times',
            'line_search'
        ]
        super(BruteHyperOptimizer, self).check_set_keys(**kwargs)

    # generate param_space for hyperopt from experiment_spec
    def generate_param_space(self):
        if self.line_search:
            param_grid = param_line_search(self.experiment_spec)
        else:
            param_grid = param_product(self.experiment_spec)
        self.param_space = generate_experiment_spec_grid(
            self.experiment_spec, param_grid)
        self.num_of_trials = len(self.param_space)

        self.trial_array = []
        for e in range(self.num_of_trials):
            experiment_spec = self.param_space[e]
            trial = self.Trial(
                experiment_spec, times=self.times, trial_num=e,
                num_of_trials=self.num_of_trials,
                run_timestamp=self.run_timestamp,
                experiment_id_override=self.experiment_id_override)
            self.trial_array.append(trial)

        return self.param_space

    def mp_run_helper(self, trial):
        return trial.run()

    def run(self):
        p = mp.Pool(PARALLEL_PROCESS_NUM)
        experiment_data = list(
            p.map(self.mp_run_helper, self.trial_array))
        p.close()
        p.join()
        return experiment_data