from asynch_mb.logger import logger
from asynch_mb.algos.base import Algo
from asynch_mb.optimizers.rl2_first_order_optimizer import RL2FirstOrderOptimizer
from asynch_mb.optimizers.first_order_optimizer import FirstOrderOptimizer
from asynch_mb.utils import Serializable

import tensorflow as tf
from collections import OrderedDict


class PPO(Algo, Serializable):
    """
    Algorithm for PPO MAML

    Args:
        policy (Policy): policy object
        name (str): tf variable scope
        learning_rate (float): learning rate for the meta-objective
        exploration (bool): use exploration / pre-update sampling term / E-MAML term
        inner_type (str): inner optimization objective - either log_likelihood or likelihood_ratio
        inner_lr (float) : gradient step size used for inner step
        meta_batch_size (int): number of meta-learning tasks
        num_inner_grad_steps (int) : number of gradient updates taken per maml iteration
        trainable_inner_step_size (boolean): whether make the inner step size a trainable variable
    """
    def __init__(
            self,
            policy,
            name="ppo",
            learning_rate=1e-3,
            clip_eps=0.2,
            max_epochs=5,
            entropy_bonus=0.,
            **kwargs
            ):
        Serializable.quick_init(self, locals())
        super(PPO, self).__init__(policy)

        self.recurrent = getattr(self.policy, 'recurrent', False)
        if self.recurrent:
            backprop_steps = kwargs.get('backprop_steps', 32)
            self.optimizer = RL2FirstOrderOptimizer(learning_rate=learning_rate, max_epochs=max_epochs,
                                                    backprop_steps=backprop_steps)
        else:
            self.optimizer = FirstOrderOptimizer(learning_rate=learning_rate, max_epochs=max_epochs)
        self._optimization_keys = ['observations', 'actions', 'advantages', 'agent_infos']
        self.name = name
        self._clip_eps = clip_eps
        self.entropy_bonus = entropy_bonus

        self.build_graph()

    def build_graph(self):
        """
        Creates the computation graph

        Notes:
            Pseudocode:
            for task in meta_batch_size:
                make_vars
                init_init_dist_sym
            for step in num_inner_grad_steps:
                for task in meta_batch_size:
                    make_vars
                    update_init_dist_sym
            set objectives for optimizer
        """

        """ Create Variables """

        """ ----- Build graph for the meta-update ----- """
        self.op_phs_dict = OrderedDict()
        obs_ph, action_ph, adv_ph, dist_info_old_ph, all_phs_dict = self._make_input_placeholders('train',
                                                                                                  recurrent=self.recurrent)
        self.op_phs_dict.update(all_phs_dict)

        if self.recurrent:
            distribution_info_vars, hidden_ph, next_hidden_var = self.policy.distribution_info_sym(obs_ph)
        else:
            distribution_info_vars = self.policy.distribution_info_sym(obs_ph)
            hidden_ph, next_hidden_var = None, None

        """ Outer objective """
        likelihood_ratio = self.policy.distribution.likelihood_ratio_sym(action_ph, dist_info_old_ph,
                                                                         distribution_info_vars)

        clipped_obj = tf.minimum(likelihood_ratio * adv_ph,
                                 tf.clip_by_value(likelihood_ratio,
                                                  1 - self._clip_eps,
                                                  1 + self._clip_eps ) * adv_ph)
        surr_obj = - tf.reduce_mean(clipped_obj) - self.entropy_bonus * \
                   tf.reduce_mean(self.policy.distribution.entropy_sym(distribution_info_vars))

        self.optimizer.build_graph(
            loss=surr_obj,
            target=self.policy,
            input_ph_dict=self.op_phs_dict,
            hidden_ph=hidden_ph,
            next_hidden_var=next_hidden_var
        )

    def optimize_policy(self, samples_data, log=True, prefix='', verbose=False):
        """
        Performs MAML outer step

        Args:
            samples_data (list) : list of lists of lists of samples (each is a dict) split by gradient update and
             meta task
            log (bool) : whether to log statistics

        Returns:
            None
        """
        input_dict = self._extract_input_dict(samples_data, self._optimization_keys, prefix='train')

        if verbose: logger.log("Optimizing")
        loss_before = self.optimizer.optimize(input_val_dict=input_dict)

        if verbose: logger.log("Computing statistics")
        loss_after = self.optimizer.loss(input_val_dict=input_dict)

        if log:
            logger.logkv(prefix+'LossBefore', loss_before)
            logger.logkv(prefix+'LossAfter', loss_after)

    def __getstate__(self):
        state = dict()
        state['init_args'] = Serializable.__getstate__(self)
        print('getstate\n')
        print(state['init_args'])
        state['policy'] = self.policy.__getstate__()
        state['optimizer'] = self.optimizer.__getstate__()
        return state

    def __setstate__(self, state):
        Serializable.__setstate__(self, state['init_args'])
        self.policy.__setstate__(state['policy'])
        self.optimizer.__getstate__(state['optimizer'])
